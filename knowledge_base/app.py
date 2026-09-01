from __future__ import annotations

import os
import hmac
import re
import uuid
from pathlib import Path
from typing import Any

import streamlit as st

from . import db
from . import ai_tutor
from . import network_access
from .editor_component import block_editor
from .importers import run_initial_imports


APP_CSS = """
<style>
    :root {
        --kb-ink: #2d2926;
        --kb-muted: #756e68;
        --kb-line: #ded8d0;
        --kb-paper: #fffdf8;
        --kb-canvas: #f7f4ee;
        --kb-accent: #c15f3c;
        --kb-accent-soft: #f4e5dc;
    }
    .stApp {
        color: var(--kb-ink);
        background:
            radial-gradient(circle at 78% -10%, rgba(222, 174, 142, .18), transparent 34rem),
            radial-gradient(circle at 30% 108%, rgba(191, 151, 117, .10), transparent 38rem),
            var(--kb-canvas);
    }
    [data-testid="stMainBlockContainer"] {
        max-width: 1160px;
        padding-top: 2.4rem;
        padding-bottom: 5rem;
    }
    [data-testid="stSidebar"] {
        border-right: 1px solid rgba(93, 78, 67, .13);
        background: rgba(239, 235, 228, .93);
        box-shadow: 8px 0 30px rgba(69, 55, 44, .035);
        backdrop-filter: blur(18px);
    }
    [data-testid="stSidebar"] .stButton > button {
        justify-content: flex-start;
        width: 100%;
        min-height: 2.1rem;
        padding: .3rem .6rem;
        border: 0;
        border-radius: 8px;
        color: #49413b;
        background: transparent;
        text-align: left;
    }
    [data-testid="stSidebar"] .stButton > button:hover {
        color: #8e412a;
        background: rgba(193, 95, 60, .09);
    }
    [data-testid="stSidebar"] [data-testid="stExpander"] {
        overflow: hidden;
        margin-bottom: .3rem;
        border: 1px solid transparent;
        border-radius: 10px;
        background: rgba(255, 253, 248, .34);
    }
    [data-testid="stSidebar"] [data-testid="stExpander"]:hover {
        border-color: rgba(122, 98, 81, .12);
        background: rgba(255, 253, 248, .6);
    }
    .kb-brand {
        margin: .2rem 0 .08rem;
        color: #332d29;
        font-family: Georgia, "Noto Serif SC", serif;
        font-size: 1.42rem;
        font-weight: 650;
        letter-spacing: -.025em;
    }
    .kb-brand-sub {
        margin-bottom: 1.15rem;
        color: #8c8178;
        font-size: .76rem;
    }
    .kb-breadcrumb {
        color: #8b817a;
        font-size: .82rem;
        margin: .15rem 0 .6rem;
    }
    h1, h2, h3 {
        color: #302b27;
        letter-spacing: -.025em;
    }
    h1 { font-family: Georgia, "Noto Serif SC", serif; }
    .stButton > button, .stDownloadButton > button {
        border-color: var(--kb-line);
        border-radius: 9px;
        color: #4a413b;
        background: rgba(255, 253, 248, .75);
        transition: border-color .15s ease, background .15s ease, transform .15s ease;
    }
    .stButton > button:hover, .stDownloadButton > button:hover {
        border-color: #ce9b84;
        color: #8f432c;
        background: #fffaf5;
    }
    .stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] {
        border-color: var(--kb-accent);
        color: white;
        background: var(--kb-accent);
    }
    [data-baseweb="input"] > div,
    [data-baseweb="textarea"] > div,
    [data-baseweb="select"] > div {
        border-color: var(--kb-line);
        border-radius: 10px;
        background: rgba(255, 253, 248, .72);
    }
    .kb-empty {
        max-width: 680px;
        margin: 5rem auto;
        padding: 3rem;
        border: 1px solid var(--kb-line);
        border-radius: 22px;
        background: rgba(255, 253, 248, .78);
        box-shadow: 0 22px 60px rgba(86, 66, 52, .07);
        text-align: center;
    }
    .kb-empty h2 { color: #50382e; }
    .kb-result {
        margin: .6rem 0;
        padding: .95rem 1.1rem;
        border: 1px solid var(--kb-line);
        border-radius: 13px;
        background: rgba(255, 253, 248, .78);
        box-shadow: 0 8px 28px rgba(81, 60, 47, .035);
    }
    .kb-result-title { color: #3a302a; font-weight: 700; }
    .kb-result-meta { color: #92877e; font-size: .76rem; }
    .kb-result-snippet { margin-top: .32rem; color: #635a54; font-size: .9rem; }
    .kb-reader {
        max-width: 860px;
        margin: 0 auto;
        padding: 1.5rem 2rem 4rem;
        border: 1px solid rgba(119, 96, 79, .11);
        border-radius: 20px;
        background: rgba(255, 253, 248, .68);
        box-shadow: 0 24px 70px rgba(88, 65, 50, .055);
    }
    .kb-reader-title {
        margin: .5rem 0 2rem;
        color: #302a26;
        font-family: Georgia, "Noto Serif SC", serif;
        font-size: clamp(2.2rem,5vw,3.35rem);
        font-weight: 650;
        letter-spacing: -.04em;
    }
    .kb-meta-chip {
        display: inline-block;
        margin: 0 .28rem .28rem 0;
        padding: .18rem .5rem;
        border-radius: 999px;
        color: #93492f;
        background: var(--kb-accent-soft);
        font-size: .75rem;
    }
    .kb-subject-archive-note {
        margin: .45rem .1rem .6rem;
        color: #8a7d74;
        font-size: .72rem;
        line-height: 1.45;
    }
    .kb-study-note {
        margin: .15rem 0 .8rem;
        color: #81766e;
        font-size: .78rem;
    }
    .kb-tutor-heading {
        margin: .1rem 0 .15rem;
        color: #45372f;
        font-family: Georgia, "Noto Serif SC", serif;
        font-size: 1.35rem;
        font-weight: 650;
    }
    .kb-tutor-sub {
        margin-bottom: .7rem;
        color: #8b7d74;
        font-size: .78rem;
        line-height: 1.55;
    }
    [data-testid="stChatMessage"] {
        border: 1px solid rgba(119, 96, 79, .11);
        border-radius: 12px;
        background: rgba(255, 253, 248, .62);
    }
    .kb-tablet-url {
        margin: .35rem 0 .6rem;
        padding: .55rem .7rem;
        border: 1px solid rgba(119, 96, 79, .15);
        border-radius: 9px;
        color: #5a4438;
        background: rgba(255, 253, 248, .72);
        font-family: Consolas, monospace;
        font-size: .78rem;
        overflow-wrap: anywhere;
        user-select: all;
    }
    @media (max-width: 1100px) {
        [data-testid="stMainBlockContainer"] {
            padding-left: 1rem;
            padding-right: 1rem;
        }
        .kb-reader { padding-left: 1rem; padding-right: 1rem; }
        [data-testid="stHorizontalBlock"]:has(.kb-tutor-heading) {
            flex-wrap: wrap;
            gap: 1.2rem;
        }
        [data-testid="stHorizontalBlock"]:has(.kb-tutor-heading)
        > [data-testid="stColumn"] {
            flex: 1 1 100% !important;
            width: 100% !important;
            min-width: 100% !important;
        }
    }
    @media (max-width: 900px) {
        [data-testid="stSidebar"],
        [data-testid="stSidebar"] > div:first-child {
            width: 235px !important;
            min-width: 235px !important;
        }
    }
    @media (max-width: 760px) {
        [data-testid="stMainBlockContainer"] {
            padding-top: 1rem;
            padding-left: .65rem;
            padding-right: .65rem;
        }
        .kb-reader {
            padding: .8rem .75rem 3rem;
            border-radius: 13px;
        }
        .kb-reader-title { font-size: 2rem; }
        .kb-empty { margin: 2rem auto; padding: 1.3rem .9rem; }
        .stButton > button { min-height: 2.65rem; }
    }
</style>
"""


def run() -> None:
    st.set_page_config(
        page_title="MyNotebook",
        page_icon=str(db.ROOT / "notebook_icon.ico"),
        layout="wide",
        initial_sidebar_state="auto",
    )
    st.markdown(APP_CSS, unsafe_allow_html=True)
    if not _require_cloud_login():
        return
    db.init_schema()
    _restore_page_from_query()
    if st.session_state.pop("kb_clear_search", False):
        st.session_state.kb_search = ""
    try:
        import_report = run_initial_imports()
    except Exception as exc:
        import_report = {}
        st.error(f"首次迁移未完成：{exc}")

    _render_sidebar()
    view = st.session_state.get("kb_view", "notes")
    search_text = st.session_state.get("kb_search", "").strip()
    if search_text:
        _render_search(search_text)
    elif view == "settings":
        _render_settings(import_report)
    elif view == "trash":
        _render_trash()
    else:
        page_id = st.session_state.get("kb_page_id")
        page = db.get_page(int(page_id)) if page_id else None
        if page and not page["archived"]:
            _render_page(page)
        else:
            _render_home(import_report)


def _require_cloud_login() -> bool:
    """Keep a public Streamlit endpoint from exposing private study notes."""
    try:
        expected = str(st.secrets.get("APP_PASSWORD", "")).strip()
    except Exception:
        expected = ""
    expected = expected or str(os.environ.get("APP_PASSWORD", "")).strip()
    if not expected:
        return True
    if st.session_state.get("kb_authenticated") is True:
        return True

    st.markdown(
        "<div style='max-width:430px;margin:9vh auto 1rem;text-align:center'>"
        "<div class='kb-brand' style='font-size:2rem'>MyNotebook</div>"
        "<div class='kb-brand-sub'>你的私人云端学习空间</div></div>",
        unsafe_allow_html=True,
    )
    with st.form("kb_cloud_login", clear_on_submit=False):
        password = st.text_input(
            "访问密码",
            type="password",
            placeholder="输入访问密码",
            autocomplete="current-password",
        )
        submitted = st.form_submit_button(
            "进入笔记本", type="primary", use_container_width=True
        )
    if submitted:
        if hmac.compare_digest(password, expected):
            st.session_state.kb_authenticated = True
            st.rerun()
        else:
            st.error("密码不正确，请重新输入。")
    st.caption("登录状态只保存在当前浏览器会话中。")
    return False


def _restore_page_from_query() -> None:
    if "kb_page_id" in st.session_state:
        return
    raw = st.query_params.get("page")
    try:
        st.session_state.kb_page_id = int(raw) if raw else None
    except (TypeError, ValueError):
        st.session_state.kb_page_id = None


def _select_page(page_id: int) -> None:
    st.session_state.kb_page_id = int(page_id)
    st.session_state.kb_view = "notes"
    st.session_state.kb_clear_search = True
    st.query_params["page"] = str(page_id)


def _render_sidebar() -> None:
    with st.sidebar:
        st.markdown("<div class='kb-brand'>MyNotebook</div>", unsafe_allow_html=True)
        st.markdown(
            "<div class='kb-brand-sub'>先写下来，再让结构自然生长</div>",
            unsafe_allow_html=True,
        )
        st.text_input(
            "搜索",
            key="kb_search",
            placeholder="搜索标题、正文、标签…",
            label_visibility="collapsed",
        )
        c1, c2 = st.columns(2)
        if c1.button("＋ 新页面", use_container_width=True):
            _new_page_dialog()
        if c2.button("⌂ 首页", use_container_width=True):
            st.session_state.kb_page_id = None
            st.session_state.kb_view = "notes"
            st.session_state.kb_clear_search = True
            st.query_params.clear()
            st.rerun()

        subjects = db.list_subjects()
        for subject in subjects:
            pages = db.list_pages(int(subject["id"]))
            if not pages:
                continue
            selected = st.session_state.get("kb_page_id")
            page_ids = {int(page["id"]) for page in pages}
            with st.expander(
                f"{subject['name']}  ·  {len(pages)}",
                expanded=selected in page_ids,
            ):
                _render_page_tree(pages)
                st.markdown(
                    "<div class='kb-subject-archive-note'>学完了？归档后内容仍会完整保留。</div>",
                    unsafe_allow_html=True,
                )
                if st.button(
                    "收起这个学科",
                    key=f"archive_subject_{subject['id']}",
                    help="从日常列表和搜索中隐藏，之后可以随时恢复",
                ):
                    db.archive_subject(int(subject["id"]), True)
                    if selected in page_ids:
                        st.session_state.kb_page_id = None
                        st.query_params.clear()
                    st.rerun()

        archived_subjects = db.list_subjects(True)
        if archived_subjects:
            with st.expander(f"已归档学科  ·  {len(archived_subjects)}", expanded=False):
                st.caption("已学完的内容暂时住在这里，不参与日常搜索。")
                for subject in archived_subjects:
                    left, right = st.columns([4, 2])
                    left.markdown(
                        f"**{_escape(subject['name'])}**  \n"
                        f"<span style='color:#94877e;font-size:.72rem'>"
                        f"{subject['page_count']} 个页面</span>",
                        unsafe_allow_html=True,
                    )
                    if right.button(
                        "恢复",
                        key=f"restore_subject_{subject['id']}",
                        use_container_width=True,
                    ):
                        db.archive_subject(int(subject["id"]), False)
                        st.rerun()

        st.divider()
        if st.button("🗑 回收站"):
            st.session_state.kb_view = "trash"
            st.session_state.kb_clear_search = True
            st.rerun()
        if st.button("⚙ 备份、导出与迁移"):
            st.session_state.kb_view = "settings"
            st.session_state.kb_clear_search = True
            st.rerun()
        _render_tablet_access()


def _render_tablet_access() -> None:
    with st.expander("📱 在平板上打开", expanded=False):
        url = (
            str(os.environ.get("MYNOTEBOOK_PUBLIC_URL") or "").strip()
            if db.CLOUD_MODE
            else network_access.tablet_url()
        )
        if not url:
            if db.CLOUD_MODE:
                st.caption("云端同步已开启；请在平板打开当前浏览器中的同一网址。")
            else:
                st.caption("暂时没有检测到可用的局域网地址。请先让电脑连接 Wi-Fi。")
            return
        st.caption(
            "在平板浏览器打开这个私人云端地址："
            if db.CLOUD_MODE
            else "电脑和平板连接同一个 Wi-Fi 后，在平板浏览器打开："
        )
        st.markdown(f"<div class='kb-tablet-url'>{_escape(url)}</div>", unsafe_allow_html=True)
        qr = network_access.qr_png(url)
        if qr:
            st.image(qr, caption="用平板相机扫码", width=170)
        if db.CLOUD_MODE:
            st.caption("笔记和图片保存在云端，家里电脑关机也可以继续使用。")
        else:
            st.caption("数据仍只存于这台电脑，两端看到的是同一份内容。电脑必须保持开机。")
            st.warning("不要在电脑和平板上同时编辑同一个页面；不同页面可以正常使用。")


def _render_page_tree(pages: list[dict[str, Any]]) -> None:
    children: dict[int | None, list[dict[str, Any]]] = {}
    page_ids = {int(page["id"]) for page in pages}
    for page in pages:
        parent = page["parent_id"]
        if parent is not None and int(parent) not in page_ids:
            parent = None
        children.setdefault(parent, []).append(page)

    for group in children.values():
        group.sort(key=lambda item: (float(item["sort_order"]), int(item["id"])))

    selected_id = st.session_state.get("kb_page_id")
    branch_cache: dict[int, bool] = {}

    def branch_contains_selected(page_id: int) -> bool:
        if page_id in branch_cache:
            return branch_cache[page_id]
        result = page_id == selected_id or any(
            branch_contains_selected(int(child["id"]))
            for child in children.get(page_id, [])
        )
        branch_cache[page_id] = result
        return result

    def walk(parent_id: int | None, depth: int, seen: set[int]) -> None:
        for page in children.get(parent_id, []):
            page_id = int(page["id"])
            if page_id in seen:
                continue
            seen.add(page_id)
            has_children = bool(children.get(page_id))
            expand_key = f"tree_expanded_{page_id}"
            expanded = (
                bool(st.session_state.get(expand_key, branch_contains_selected(page_id)))
                if has_children
                else False
            )
            icon = ("▾" if expanded else "▸") if has_children else "·"
            label = f"{'　' * min(depth, 5)}{icon} {page['title']}"
            if st.button(label, key=f"tree_page_{page_id}", help=page["kind"] or None):
                if has_children:
                    st.session_state[expand_key] = not expanded
                _select_page(page_id)
                st.rerun()
            if expanded:
                walk(page_id, depth + 1, seen)

    walk(None, 0, set())


@st.dialog("新建页面")
def _new_page_dialog() -> None:
    subjects = db.list_subjects()
    subject_options = [subject["name"] for subject in subjects]
    mode = st.radio(
        "学科",
        ["选择已有学科", "新建学科"],
        horizontal=True,
        label_visibility="collapsed",
    )
    if mode == "选择已有学科" and subject_options:
        subject_name = st.selectbox("所属学科", subject_options)
    else:
        subject_name = st.text_input("学科名称", placeholder="例如：机器人学习")
    title = st.text_input("页面标题", placeholder="可以先随便写，之后随时修改")
    parent_id: int | None = None
    if mode == "选择已有学科" and subject_options and subject_name:
        subject = next(x for x in subjects if x["name"] == subject_name)
        pages = db.list_pages(int(subject["id"]))
        parent_labels = ["不设上级页面"] + [
            f"{page['title']}  ·  #{page['id']}" for page in pages
        ]
        selected_parent = st.selectbox("放在哪个页面下面（可选）", parent_labels)
        if selected_parent != "不设上级页面":
            parent_id = int(selected_parent.rsplit("#", 1)[1])
    if st.button("创建并开始写", type="primary", use_container_width=True):
        if not subject_name.strip():
            st.warning("先写一个学科名称。")
            return
        subject_id = db.ensure_subject(subject_name)
        page_id = db.create_page(
            subject_id,
            title.strip() or "未命名页面",
            parent_id,
            blocks=[{"type": "text", "content": ""}],
        )
        _select_page(page_id)
        st.rerun()


def _render_home(import_report: dict[str, Any]) -> None:
    subjects = db.list_subjects()
    archived_subjects = db.list_subjects(True)
    pages = db.list_pages()
    if import_report:
        imported = import_report.get("legacy", {})
        docx = import_report.get("docx", [])
        details = []
        if imported:
            details.append(
                f"旧数据已无损映射：{imported.get('pages', 0)} 个页面，"
                f"{imported.get('reflections', 0)} 条复盘"
            )
        if docx:
            details.append(
                "Word 已按原顺序导入：" + "、".join(item["file"] for item in docx)
            )
        if details:
            st.success("；".join(details))

    if not pages:
        st.markdown(
            """
            <div class="kb-empty">
              <h2>从一张自由页面开始</h2>
              <p>不必先决定这是知识点、例题还是错题。先写，再通过标题、页面树、标签和双链整理。</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("创建第一张页面", type="primary"):
            _new_page_dialog()
        return

    st.title("知识工作台")
    st.caption("这里展示最近生长的页面，而不是要求你先完成一套目录。")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("正在学习", len(subjects))
    m2.metric("已归档", len(archived_subjects))
    m3.metric("活跃页面", len(pages))
    m4.metric("已有复盘", len(db.query("SELECT id FROM kb_reflections")))
    st.subheader("最近修改")
    recent = sorted(pages, key=lambda x: x["updated_at"], reverse=True)[:12]
    for page in recent:
        c1, c2 = st.columns([6, 1])
        with c1:
            st.markdown(
                f"**{page['title']}**  \n"
                f"<span style='color:#808982;font-size:.78rem'>"
                f"{page['subject_name']} · {page['updated_at']}</span>",
                unsafe_allow_html=True,
            )
        if c2.button("打开", key=f"recent_{page['id']}"):
            _select_page(int(page["id"]))
            st.rerun()


def _render_search(search_text: str) -> None:
    results = db.search_pages(search_text)
    st.title(f"搜索：{search_text}")
    st.caption(f"找到 {len(results)} 张页面。搜索覆盖标题、正文、学科和标签。")
    if not results:
        st.info("没有匹配结果。可以减少关键词，或检查内容是否还在本地草稿中尚未自动保存。")
        return
    for result in results:
        st.markdown(
            f"""
            <div class="kb-result">
              <div class="kb-result-title">{_escape(result['title'])}</div>
              <div class="kb-result-meta">{_escape(result['subject_name'])} · {_escape(result['updated_at'])}</div>
              <div class="kb-result-snippet">{_escape(result['snippet'] or '')}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("打开页面", key=f"search_{result['id']}"):
            _select_page(int(result["id"]))
            st.rerun()


def _render_page(page: dict[str, Any]) -> None:
    st.markdown(
        f"<div class='kb-breadcrumb'>{_escape(page['subject_name'])}"
        f"　/　{_escape(page['kind'] or '自由页面')}</div>",
        unsafe_allow_html=True,
    )
    top1, top2 = st.columns([5, 2])
    mode = top1.segmented_control(
        "查看方式",
        ["编辑", "学习模式", "阅读", "关联与历史"],
        default="编辑",
        key=f"page_mode_{page['id']}",
        label_visibility="collapsed",
    )
    with top2.popover("页面设置"):
        _render_page_settings(page)

    if mode == "阅读":
        _render_reader(page)
    elif mode == "关联与历史":
        _render_page_context(page)
    elif mode == "学习模式":
        _render_study_mode(page)
    else:
        _render_editor(page)


def _render_editor(
    page: dict[str, Any], insert_request: dict[str, Any] | None = None
) -> None:
    editor_key = f"kb_editor_{page['id']}"

    def on_save() -> None:
        state = st.session_state.get(editor_key, {})
        payload = state.get("save") if isinstance(state, dict) else getattr(state, "save", None)
        if not payload or int(payload.get("page_id", -1)) != int(page["id"]):
            return
        db.save_page(
            int(page["id"]),
            payload.get("title", page["title"]),
            payload.get("blocks", []),
            reason=payload.get("reason", "自动保存"),
        )

    block_editor(
        page,
        key=editor_key,
        on_save=on_save,
        insert_request=insert_request,
    )


def _render_study_mode(page: dict[str, Any]) -> None:
    pending_key = f"kb_tutor_insert_{page['id']}"
    insert_request = st.session_state.pop(pending_key, None)
    st.markdown(
        "<div class='kb-study-note'>左边持续写笔记，右边让 AI 提问、讲解和检查。"
        "AI 读取的是最近一次已保存内容；看到“已保存”后再提问，上下文最准确。</div>",
        unsafe_allow_html=True,
    )
    with st.container(key="kb_study_layout"):
        notes, tutor = st.columns([1.72, 1], gap="large")
        with notes:
            _render_editor(page, insert_request=insert_request)
        with tutor:
            _render_tutor(page, pending_key)


def _render_tutor(page: dict[str, Any], pending_key: str) -> None:
    st.markdown("<div class='kb-tutor-heading'>AI 学习导师</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='kb-tutor-sub'>默认一次只推进一个概念。回答不会自动改笔记，"
        "只有点击“插入为建议草稿”才会进入左侧。</div>",
        unsafe_allow_html=True,
    )

    with st.expander("模型与 API 设置", expanded=False):
        provider = st.selectbox(
            "服务商",
            list(ai_tutor.PROVIDERS),
            key="kb_tutor_provider",
        )
        model_key = f"kb_tutor_model_{provider}"
        if model_key not in st.session_state:
            st.session_state[model_key] = ai_tutor.default_model(provider)
        model = st.text_input("模型", key=model_key)
        api_key_state = f"kb_tutor_api_key_{provider}"
        if api_key_state not in st.session_state:
            st.session_state[api_key_state] = ai_tutor.environment_key(provider)
        api_key = st.text_input(
            "API Key",
            type="password",
            key=api_key_state,
            placeholder=f"填写 {provider} API Key",
        )
        st.caption("密钥仅保存在当前 Streamlit 运行会话，不会写入笔记数据库。")

    history_key = f"kb_tutor_history_{page['id']}"
    history = st.session_state.setdefault(history_key, [])
    if not history:
        st.info(
            "可以直接说“从本页开始带我学”，或者先写下自己的理解，再让我检查。"
        )
    else:
        with st.container(height=420, border=False):
            for index, message in enumerate(history):
                with st.chat_message(message["role"]):
                    st.markdown(message["content"])
                    if message["role"] == "assistant":
                        if st.button(
                            "插入为建议草稿",
                            key=f"tutor_insert_{page['id']}_{index}",
                            help="作为“想法”内容块插到笔记末尾，不会覆盖原内容。",
                        ):
                            st.session_state[pending_key] = {
                                "nonce": uuid.uuid4().hex,
                                "page_id": int(page["id"]),
                                "content": message["content"],
                                "block_type": "callout",
                                "provider": provider,
                            }
                            st.rerun()

    quick1, quick2 = st.columns(2)
    quick_prompt = None
    if quick1.button("从本页开始", use_container_width=True, key=f"start_{page['id']}"):
        quick_prompt = (
            "请先判断这页正在学习什么，只选择一个最基础、最关键的概念开始。"
            "先用一个问题检查我的理解，不要一上来完整讲答案。"
        )
    if quick2.button("检查我的理解", use_container_width=True, key=f"check_{page['id']}"):
        quick_prompt = (
            "请检查当前笔记里我自己的理解：先指出一个最值得修正或确认的地方，"
            "然后问我一个具体问题，不要直接重写整页。"
        )

    question = st.text_area(
        "向导师提问",
        key=f"kb_tutor_question_{page['id']}",
        placeholder="例如：为什么独立一定推出不相关，反过来却不一定？",
        height=100,
        label_visibility="collapsed",
    )
    send, clear = st.columns([3, 1])
    if send.button("发送", type="primary", use_container_width=True, key=f"send_{page['id']}"):
        quick_prompt = question.strip()
    if clear.button("清空", use_container_width=True, key=f"clear_{page['id']}"):
        st.session_state[history_key] = []
        st.rerun()

    if quick_prompt:
        if not api_key.strip():
            st.warning(f"先在“模型与 API 设置”中填写 {provider} API Key。")
            return
        history.append({"role": "user", "content": quick_prompt})
        try:
            with st.spinner("导师正在结合当前笔记思考…"):
                answer = ai_tutor.tutor_reply(
                    provider=provider,
                    api_key=api_key,
                    model=model,
                    page=page,
                    messages=history,
                )
        except ai_tutor.TutorError as exc:
            history.pop()
            st.error(str(exc))
            return
        history.append({"role": "assistant", "content": answer})
        st.rerun()


def _render_reader(page: dict[str, Any]) -> None:
    st.markdown("<div class='kb-reader'>", unsafe_allow_html=True)
    st.markdown(
        f"<div class='kb-reader-title'>{_escape(page['title'])}</div>",
        unsafe_allow_html=True,
    )
    if page["tags"]:
        st.markdown(
            "".join(
                f"<span class='kb-meta-chip'>{_escape(tag)}</span>" for tag in page["tags"]
            ),
            unsafe_allow_html=True,
        )
    for block in page["blocks"]:
        kind = block["type"]
        content = block["content"] or ""
        if kind == "heading1":
            st.markdown(f"# {content}")
        elif kind == "heading2":
            st.markdown(f"## {content}")
        elif kind == "heading3":
            st.markdown(f"### {content}")
        elif kind == "equation":
            try:
                st.latex(content)
            except Exception:
                st.code(content, language="latex")
        elif kind == "callout":
            st.info(content)
        elif kind == "divider":
            st.divider()
        elif kind == "image" and block["asset_path"]:
            image_source = (
                block.get("asset_url")
                if db.CLOUD_MODE
                else db.STATIC_DIR / block["asset_path"]
            )
            if db.CLOUD_MODE or image_source.exists():
                st.image(image_source, caption=content or None, use_container_width=True)
            else:
                st.warning(f"图片文件缺失：{block['asset_path']}")
        elif content:
            st.markdown(content)
    st.markdown("</div>", unsafe_allow_html=True)


def _render_page_context(page: dict[str, Any]) -> None:
    left, right = st.columns([3, 2])
    with left:
        st.subheader("复盘记录")
        for reflection in page["reflections"]:
            with st.container(border=True):
                st.caption(reflection["created_at"])
                st.markdown(reflection["content"])
        reflection = st.text_area(
            "这次重看后，我改变了什么理解？",
            key=f"reflection_{page['id']}",
            placeholder="不要只写“复习完成”，记录判断发生了什么变化。",
        )
        if st.button("记录本次复盘", key=f"add_reflection_{page['id']}"):
            if reflection.strip():
                db.add_reflection(int(page["id"]), reflection)
                st.session_state[f"reflection_{page['id']}"] = ""
                st.rerun()

        st.subheader("链接关系")
        outgoing = db.get_outgoing_links(int(page["id"]))
        backlinks = db.get_backlinks(int(page["id"]))
        st.caption("在正文输入 `[[页面名]]`，保存后会在这里形成关系。")
        _render_link_list("这页链接到", outgoing, f"out_{page['id']}")
        _render_link_list("哪些页面提到这里", backlinks, f"back_{page['id']}")

    with right:
        st.subheader("标签")
        tag_text = st.text_input(
            "用逗号分隔",
            value=", ".join(page["tags"]),
            key=f"tags_{page['id']}",
        )
        if st.button("保存标签", key=f"save_tags_{page['id']}"):
            db.set_tags(int(page["id"]), re.split(r"[,，]", tag_text))
            st.rerun()

        st.subheader("版本历史")
        versions = db.list_versions(int(page["id"]))
        if not versions:
            st.caption("页面发生第一次保存修改后，这里会出现历史版本。")
        for version in versions:
            with st.expander(f"{version['saved_at']} · {version['reason']}"):
                st.caption(f"当时标题：{version['title']}")
                if st.button(
                    "恢复到这个版本",
                    key=f"restore_{page['id']}_{version['id']}",
                ):
                    db.restore_version(int(page["id"]), int(version["id"]))
                    st.success("已恢复；恢复前的当前状态也已进入版本历史。")
                    st.rerun()


def _render_link_list(title: str, links: list[dict[str, Any]], key_prefix: str) -> None:
    st.markdown(f"**{title}（{len(links)}）**")
    if not links:
        st.caption("暂无")
    for link in links:
        if st.button(
            f"{link['subject_name']} / {link['title']}",
            key=f"{key_prefix}_{link['id']}",
        ):
            _select_page(int(link["id"]))
            st.rerun()


def _render_page_settings(page: dict[str, Any]) -> None:
    st.caption("页面可以随时换学科、换父页面；这里的结构不是录入门槛。")
    subjects = db.list_subjects()
    subject_names = [subject["name"] for subject in subjects]
    current_index = subject_names.index(page["subject_name"])
    subject_name = st.selectbox(
        "所属学科",
        subject_names,
        index=current_index,
        key=f"move_subject_{page['id']}",
    )
    target_subject = next(x for x in subjects if x["name"] == subject_name)
    candidate_pages = [
        candidate
        for candidate in db.list_pages(int(target_subject["id"]))
        if int(candidate["id"]) != int(page["id"])
        and int(candidate["id"])
        not in {int(x["id"]) for x in db.descendants_of(int(page["id"]))}
    ]
    options = ["不设父页面"] + [
        f"{candidate['title']} · #{candidate['id']}" for candidate in candidate_pages
    ]
    selected_parent = st.selectbox(
        "父页面",
        options,
        key=f"move_parent_{page['id']}",
    )
    if st.button("应用移动", key=f"move_apply_{page['id']}"):
        parent_id = (
            None
            if selected_parent == "不设父页面"
            else int(selected_parent.rsplit("#", 1)[1])
        )
        db.move_page(
            int(page["id"]),
            new_parent_id=parent_id,
            new_subject_id=int(target_subject["id"]),
        )
        st.rerun()
    st.divider()
    st.warning("移入回收站会同时收起它的子页面，但不会删除内容。")
    if st.button("移入回收站", key=f"archive_{page['id']}"):
        db.archive_page(int(page["id"]), True)
        st.session_state.kb_page_id = None
        st.query_params.clear()
        st.rerun()


def _render_trash() -> None:
    st.title("回收站")
    st.caption("这里只做软删除。恢复父页面时，其子页面也会一起恢复。")
    pages = db.list_pages(archived=True, include_archived_subjects=True)
    if not pages:
        st.info("回收站是空的。")
        return
    for page in pages:
        c1, c2 = st.columns([6, 1])
        c1.markdown(f"**{page['title']}**  \n{page['subject_name']}")
        if c2.button("恢复", key=f"unarchive_{page['id']}"):
            db.archive_page(int(page["id"]), False)
            st.rerun()


def _render_settings(import_report: dict[str, Any]) -> None:
    st.title("备份、导出与迁移")
    st.subheader("完整备份")
    st.caption("备份包含 SQLite 数据库和新知识库的全部附件；不会改动当前数据。")
    if "kb_backup_payload" not in st.session_state:
        if st.button("生成完整备份"):
            payload, filename = db.create_full_backup()
            st.session_state.kb_backup_payload = payload
            st.session_state.kb_backup_filename = filename
            st.rerun()
    else:
        st.download_button(
            "下载备份 ZIP",
            data=st.session_state.kb_backup_payload,
            file_name=st.session_state.kb_backup_filename,
            mime="application/zip",
            type="primary",
        )
        if st.button("重新生成"):
            del st.session_state.kb_backup_payload
            del st.session_state.kb_backup_filename
            st.rerun()

    st.divider()
    st.subheader("按学科导出")
    subjects = db.list_subjects(None)
    if subjects:
        subject_name = st.selectbox(
            "选择学科",
            [subject["name"] for subject in subjects],
            key="export_subject",
        )
        subject = next(x for x in subjects if x["name"] == subject_name)
        payload, filename = db.export_subject(int(subject["id"]))
        st.download_button(
            "导出 Markdown + 附件",
            data=payload,
            file_name=filename,
            mime="application/zip",
        )

    st.divider()
    st.subheader("迁移状态")
    migrations = db.query(
        "SELECT migration_key,completed_at,details_json FROM kb_migrations "
        "ORDER BY completed_at"
    )
    for migration in migrations:
        if migration["migration_key"].startswith("backup:"):
            label = "迁移前备份"
        elif migration["migration_key"].startswith("docx:"):
            label = "Word 导入"
        else:
            label = "旧数据库映射"
        st.markdown(f"✅ **{label}**　{migration['completed_at']}")
        with st.expander("查看记录", expanded=False):
            st.code(migration["details_json"], language="json")
    st.info(
        "旧版代码保存在 legacy_app.py；旧表 entries、reviews、chapters、notes、"
        "subjects 等均未删除。新系统只写入 kb_* 表。"
    )


def _escape(value: Any) -> str:
    import html

    return html.escape(str(value or ""))
