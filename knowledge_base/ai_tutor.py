from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


PROVIDERS = {
    "DeepSeek": {
        "url": "https://api.deepseek.com/chat/completions",
        "model": "deepseek-chat",
        "env": "DEEPSEEK_API_KEY",
    },
    "OpenAI": {
        "url": "https://api.openai.com/v1/chat/completions",
        "model": "gpt-5-mini",
        "env": "OPENAI_API_KEY",
    },
}


class TutorError(RuntimeError):
    """A user-facing AI tutor request error."""


def default_model(provider: str) -> str:
    return str(PROVIDERS.get(provider, PROVIDERS["DeepSeek"])["model"])


def environment_key(provider: str) -> str:
    config = PROVIDERS.get(provider, PROVIDERS["DeepSeek"])
    return os.environ.get(str(config["env"]), "")


def page_context(page: dict[str, Any], max_chars: int = 24000) -> str:
    """Build a bounded, text-only snapshot of the saved page."""
    lines = [
        f"学科：{page.get('subject_name', '')}",
        f"页面：{page.get('title', '')}",
        f"页面类型：{page.get('kind') or '自由页面'}",
        "",
        "当前已保存笔记：",
    ]
    labels = {
        "heading1": "一级标题",
        "heading2": "二级标题",
        "heading3": "三级标题",
        "equation": "公式",
        "callout": "想法",
        "text": "正文",
        "image": "图片说明",
    }
    for block in page.get("blocks", []):
        content = str(block.get("content") or "").strip()
        if not content:
            continue
        label = labels.get(str(block.get("type")), "内容")
        lines.append(f"[{label}] {content}")
    value = "\n".join(lines)
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "\n\n[笔记过长，后续内容未发送给模型]"


def tutor_reply(
    *,
    provider: str,
    api_key: str,
    model: str,
    page: dict[str, Any],
    messages: list[dict[str, str]],
) -> str:
    config = PROVIDERS.get(provider)
    if not config:
        raise TutorError("暂不支持这个模型服务商。")
    if not api_key.strip():
        raise TutorError(f"请先填写 {provider} API Key。")
    if not model.strip():
        raise TutorError("请填写模型名称。")

    system_prompt = f"""
你是这位学生的长期大学课程与机器人科研导师。学生正在自己的知识库里边学边写。

教学原则：
- 一次只推进一个核心概念，先直觉和为什么，再讲机制或公式。
- 不要替学生包办笔记；优先追问他的理解，让他先预测、解释或完成一个小步骤。
- 如果学生理解有误，直接指出具体错误与原因，不要盲目赞同。
- 数学公式使用 Markdown/LaTeX。新符号先说明含义。
- 回答尽量紧凑，通常控制在 500 个中文字符内；只有学生明确要求时才展开。
- 结合当前笔记回答，但笔记可能包含学生尚未确认的猜想，不要把它当成权威事实。
- 不要声称你修改或保存了笔记。你只能给出建议草稿，是否插入由学生决定。
- 合适时把概率论、数学基础与机器学习、机器人感知或控制联系起来，但不要牵强。

{page_context(page)}
""".strip()
    request_messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt}
    ]
    for message in messages[-12:]:
        role = message.get("role")
        content = str(message.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            request_messages.append({"role": role, "content": content})

    payload = json.dumps(
        {"model": model.strip(), "messages": request_messages, "stream": False},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        str(config["url"]),
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
            "User-Agent": "MyNotebook/2.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            error_payload = json.loads(exc.read().decode("utf-8", errors="replace"))
            detail = str(error_payload.get("error", {}).get("message") or "")
        except Exception:
            pass
        suffix = f"：{detail[:240]}" if detail else ""
        raise TutorError(f"{provider} 请求失败（HTTP {exc.code}）{suffix}") from exc
    except urllib.error.URLError as exc:
        raise TutorError(f"无法连接 {provider}：{exc.reason}") from exc
    except TimeoutError as exc:
        raise TutorError(f"{provider} 响应超时，请稍后再试。") from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise TutorError(f"{provider} 返回了无法解析的数据。") from exc

    try:
        content = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise TutorError(f"{provider} 没有返回有效回答。") from exc
    if isinstance(content, list):
        content = "".join(
            str(item.get("text") or "") if isinstance(item, dict) else str(item)
            for item in content
        )
    answer = str(content or "").strip()
    if not answer:
        raise TutorError(f"{provider} 返回了空回答。")
    return answer
