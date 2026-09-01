const BLOCK_LABELS = {
  text: "段落",
  heading1: "一级标题",
  heading2: "二级标题",
  heading3: "三级标题",
  equation: "公式",
  callout: "想法 / 提醒",
  image: "图片",
  divider: "分隔线",
};

function makeUid() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID().replaceAll("-", "");
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
}

function canonicalBlocks(blocks) {
  return blocks.map((block) => ({
    uid: block.uid,
    type: block.type,
    content: block.content ?? "",
    asset_path: block.asset_path ?? "",
    metadata: block.metadata ?? {},
  }));
}

function contentSignature(title, blocks) {
  const comparable = canonicalBlocks(blocks).map((block) => ({
    ...block,
    asset_path: block.type === "image" ? "__image__" : block.asset_path,
  }));
  return JSON.stringify({ title, blocks: comparable });
}

function autoResize(textarea) {
  textarea.style.height = "0px";
  textarea.style.height = `${Math.max(36, textarea.scrollHeight + 2)}px`;
}

export default function(component) {
  const { data, parentElement, setTriggerValue } = component;
  const page = data?.page ?? {};
  const insertRequest = data?.insert_request ?? null;
  const draftKey = `mynotebook:draft:${page.id}`;
  const focusKey = `mynotebook:focus:${page.id}`;
  const titleInput = parentElement.querySelector("#kb-page-title");
  const blocksRoot = parentElement.querySelector("#kb-blocks");
  const saveButton = parentElement.querySelector("#kb-save-button");
  const saveStatus = parentElement.querySelector("#kb-save-status");
  const imageButton = parentElement.querySelector("#kb-image-button");
  const imageInput = parentElement.querySelector("#kb-image-input");
  const addLastButton = parentElement.querySelector("#kb-add-last");

  let blocks = canonicalBlocks(page.blocks ?? []);
  let title = page.title ?? "";
  let dirty = false;
  let activeIndex = Math.max(0, blocks.length - 1);
  let draggedIndex = null;
  let saveInFlight = false;
  let idleSaveTimer = null;

  try {
    const rawDraft = localStorage.getItem(draftKey);
    if (rawDraft) {
      const draft = JSON.parse(rawDraft);
      const serverSignature = contentSignature(title, blocks);
      const draftSignature = contentSignature(
        draft.title ?? "",
        draft.blocks ?? [],
      );
      if (serverSignature === draftSignature) {
        localStorage.removeItem(draftKey);
      } else if (draft.pageId === page.id) {
        const draftMatchesServerVersion = Boolean(draft.baseUpdatedAt)
          && String(draft.baseUpdatedAt) === String(page.updated_at ?? "");
        if (draftMatchesServerVersion) {
          title = draft.title ?? title;
          blocks = canonicalBlocks(draft.blocks ?? blocks);
          dirty = true;
          saveStatus.textContent = "已恢复本地草稿";
        } else {
          // Keep stale work locally, but never let it silently replace a newer
          // database version. The user can deliberately recover it later.
          saveStatus.textContent = "发现旧草稿，已保护当前版本";
        }
      }
    }
  } catch (_) {
    localStorage.removeItem(draftKey);
  }

  if (!blocks.length) {
    blocks = [{ uid: makeUid(), type: "text", content: "", asset_path: "", metadata: {} }];
  }

  titleInput.value = title;
  titleInput.placeholder = "未命名页面";

  function persistDraft() {
    const draft = {
      pageId: page.id,
      baseUpdatedAt: page.updated_at,
      savedAt: new Date().toISOString(),
      title,
      blocks: canonicalBlocks(blocks),
    };
    try {
      localStorage.setItem(draftKey, JSON.stringify(draft));
    } catch (_) {
      saveStatus.textContent = "草稿较大，将直接自动保存";
    }
  }

  function scheduleIdleSave() {
    if (idleSaveTimer !== null) clearTimeout(idleSaveTimer);
    idleSaveTimer = setTimeout(() => {
      idleSaveTimer = null;
      requestSave("停止输入 10 秒后自动保存");
    }, 10000);
  }

  function markDirty() {
    dirty = true;
    saveInFlight = false;
    saveStatus.textContent = "有未保存修改";
    persistDraft();
    scheduleIdleSave();
  }

  function rememberFocus() {
    const focused = parentElement.querySelector("textarea:focus, input:focus");
    if (!focused) return;
    const block = focused.closest("[data-uid]");
    const snapshot = {
      pageId: page.id,
      kind: focused === titleInput ? "title" : "block",
      uid: block?.dataset.uid ?? "",
      start: focused.selectionStart ?? 0,
      end: focused.selectionEnd ?? focused.selectionStart ?? 0,
      savedAt: Date.now(),
    };
    try {
      localStorage.setItem(focusKey, JSON.stringify(snapshot));
    } catch (_) {
      // Focus restoration is a convenience; draft persistence remains the safety net.
    }
  }

  function restoreFocus() {
    let snapshot = null;
    try {
      snapshot = JSON.parse(localStorage.getItem(focusKey) ?? "null");
      localStorage.removeItem(focusKey);
    } catch (_) {
      localStorage.removeItem(focusKey);
    }
    if (
      !snapshot
      || snapshot.pageId !== page.id
      || Date.now() - Number(snapshot.savedAt || 0) > 5000
    ) return;
    requestAnimationFrame(() => {
      const target = snapshot.kind === "title"
        ? titleInput
        : parentElement.querySelector(`[data-uid="${snapshot.uid}"] textarea`);
      if (!target) return;
      target.focus({ preventScroll: true });
      const length = target.value?.length ?? 0;
      target.setSelectionRange(
        Math.min(Number(snapshot.start || 0), length),
        Math.min(Number(snapshot.end || 0), length),
      );
    });
  }

  function requestSave(reason = "自动保存") {
    if (!dirty || saveInFlight) return;
    if (idleSaveTimer !== null) {
      clearTimeout(idleSaveTimer);
      idleSaveTimer = null;
    }
    rememberFocus();
    const submittedSignature = JSON.stringify({ title, blocks: canonicalBlocks(blocks) });
    saveInFlight = true;
    saveStatus.textContent = "正在保存…";
    setTriggerValue("save", {
      page_id: page.id,
      title,
      blocks: canonicalBlocks(blocks),
      reason,
      client_time: new Date().toISOString(),
    });
    setTimeout(() => {
      const currentSignature = JSON.stringify({ title, blocks: canonicalBlocks(blocks) });
      if (currentSignature === submittedSignature) {
        dirty = false;
        saveInFlight = false;
        saveStatus.textContent = "已保存";
        restoreFocus();
      }
    }, 900);
  }

  function blockTemplate(type = "text") {
    return {
      uid: makeUid(),
      type,
      content: "",
      asset_path: "",
      metadata: {},
    };
  }

  function insertBlock(index, type = "text", focus = true) {
    const block = blockTemplate(type);
    blocks.splice(index, 0, block);
    activeIndex = index;
    markDirty();
    render();
    if (focus) {
      requestAnimationFrame(() => {
        parentElement.querySelector(`[data-uid="${block.uid}"] textarea`)?.focus();
      });
    }
  }

  function removeBlock(index) {
    if (blocks.length === 1) {
      blocks[0] = blockTemplate("text");
      activeIndex = 0;
    } else {
      blocks.splice(index, 1);
      activeIndex = Math.max(0, Math.min(index, blocks.length - 1));
    }
    markDirty();
    render();
  }

  function moveBlock(from, to) {
    if (from === to || from < 0 || to < 0 || from >= blocks.length || to >= blocks.length) return;
    const [block] = blocks.splice(from, 1);
    blocks.splice(to, 0, block);
    activeIndex = to;
    markDirty();
    render();
    requestAnimationFrame(() => {
      parentElement.querySelector(`[data-uid="${block.uid}"] textarea`)?.focus();
    });
  }

  function insertMarkdown(textarea, before, after = before) {
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const value = textarea.value;
    textarea.value = value.slice(0, start) + before + value.slice(start, end) + after + value.slice(end);
    textarea.selectionStart = start + before.length;
    textarea.selectionEnd = end + before.length;
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    textarea.focus();
  }

  function readImages(files, insertAt) {
    [...files].filter((file) => file.type.startsWith("image/")).forEach((file, offset) => {
      if (file.size > 25 * 1024 * 1024) {
        alert(`图片 ${file.name} 超过 25 MB，未插入。`);
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        blocks.splice(insertAt + offset, 0, {
          uid: makeUid(),
          type: "image",
          content: file.name,
          asset_path: reader.result,
          metadata: { original_name: file.name },
        });
        activeIndex = insertAt + offset;
        markDirty();
        render();
      };
      reader.readAsDataURL(file);
    });
  }

  function createTools(block, index) {
    const tools = document.createElement("div");
    tools.className = "kb-block-tools";

    const select = document.createElement("select");
    Object.entries(BLOCK_LABELS).forEach(([value, label]) => {
      if (value === "image") return;
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      option.selected = block.type === value;
      select.appendChild(option);
    });
    select.onchange = () => {
      block.type = select.value;
      markDirty();
      render();
    };
    if (block.type !== "image") tools.appendChild(select);

    const bold = document.createElement("button");
    bold.type = "button";
    bold.textContent = "加粗";
    bold.title = "用 Markdown 加粗选中文字";
    bold.onclick = () => {
      const textarea = parentElement.querySelector(`[data-uid="${block.uid}"] textarea`);
      if (textarea) insertMarkdown(textarea, "**");
    };
    if (!["image", "divider"].includes(block.type)) tools.appendChild(bold);

    const link = document.createElement("button");
    link.type = "button";
    link.textContent = "双链";
    link.title = "插入 [[页面名]]";
    link.onclick = () => {
      const textarea = parentElement.querySelector(`[data-uid="${block.uid}"] textarea`);
      if (textarea) insertMarkdown(textarea, "[[", "]]");
    };
    if (!["image", "divider"].includes(block.type)) tools.appendChild(link);

    const duplicate = document.createElement("button");
    duplicate.type = "button";
    duplicate.textContent = "复制";
    duplicate.onclick = () => {
      blocks.splice(index + 1, 0, { ...block, uid: makeUid(), metadata: { ...(block.metadata ?? {}) } });
      markDirty();
      render();
    };
    tools.appendChild(duplicate);

    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "删除";
    remove.className = "danger";
    remove.onclick = () => removeBlock(index);
    tools.appendChild(remove);
    return tools;
  }

  function createBlock(block, index) {
    const shell = document.createElement("article");
    shell.className = `kb-block type-${block.type}`;
    shell.dataset.uid = block.uid;
    shell.dataset.index = String(index);
    shell.ondragover = (event) => {
      event.preventDefault();
      shell.classList.add("kb-drop-target");
    };
    shell.ondragleave = () => shell.classList.remove("kb-drop-target");
    shell.ondrop = (event) => {
      event.preventDefault();
      shell.classList.remove("kb-drop-target");
      if (draggedIndex !== null) moveBlock(draggedIndex, index);
    };

    const handle = document.createElement("button");
    handle.type = "button";
    handle.className = "kb-handle";
    handle.textContent = "⠿";
    handle.title = "拖动内容块";
    handle.draggable = true;
    handle.ondragstart = () => {
      draggedIndex = index;
      shell.classList.add("kb-dragging");
    };
    handle.ondragend = () => {
      draggedIndex = null;
      shell.classList.remove("kb-dragging");
    };
    shell.appendChild(handle);

    const contentWrap = document.createElement("div");
    contentWrap.className = "kb-content-wrap";
    contentWrap.appendChild(createTools(block, index));

    if (block.type === "image") {
      const imageWrap = document.createElement("div");
      imageWrap.className = "kb-image-wrap";
      const image = document.createElement("img");
      image.loading = "lazy";
      image.decoding = "async";
      image.src = block.asset_path?.startsWith("data:")
        ? block.asset_path
        : (block.asset_url || `app/static/${block.asset_path}`);
      image.alt = block.content || "笔记图片";
      imageWrap.appendChild(image);
      const caption = document.createElement("textarea");
      caption.rows = 1;
      caption.className = "kb-caption";
      caption.value = block.content ?? "";
      caption.placeholder = "图片说明（可选）";
      caption.onfocus = () => { activeIndex = index; };
      caption.oninput = () => {
        block.content = caption.value;
        autoResize(caption);
        markDirty();
      };
      imageWrap.appendChild(caption);
      contentWrap.appendChild(imageWrap);
      requestAnimationFrame(() => autoResize(caption));
    } else if (block.type === "divider") {
      const divider = document.createElement("div");
      divider.className = "kb-divider";
      contentWrap.appendChild(divider);
    } else {
      const textarea = document.createElement("textarea");
      textarea.rows = 1;
      textarea.className = "kb-textarea";
      textarea.value = block.content ?? "";
      textarea.placeholder = {
        text: "写下你的理解、问题或推导…",
        heading1: "一级标题",
        heading2: "小标题",
        heading3: "三级标题",
        equation: "输入 LaTeX，例如 E[X] = \\int x f(x)\\,dx",
        callout: "这里可以写关键判断、疑问或易错点…",
      }[block.type] ?? "写点什么…";
      textarea.onfocus = () => { activeIndex = index; };
      textarea.oninput = () => {
        block.content = textarea.value;
        if (block.type === "text") {
          if (textarea.value.startsWith("### ")) {
            block.type = "heading3";
            block.content = textarea.value.slice(4);
            render();
          } else if (textarea.value.startsWith("## ")) {
            block.type = "heading2";
            block.content = textarea.value.slice(3);
            render();
          } else if (textarea.value.startsWith("# ")) {
            block.type = "heading1";
            block.content = textarea.value.slice(2);
            render();
          }
        }
        autoResize(textarea);
        markDirty();
      };
      textarea.onpaste = (event) => {
        const images = [...(event.clipboardData?.files ?? [])].filter((file) => file.type.startsWith("image/"));
        if (images.length) {
          event.preventDefault();
          readImages(images, index + 1);
        }
      };
      textarea.onkeydown = (event) => {
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
          event.preventDefault();
          requestSave("手动保存");
        } else if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
          event.preventDefault();
          insertBlock(index + 1, "text");
        } else if (event.altKey && event.key === "ArrowUp") {
          event.preventDefault();
          moveBlock(index, Math.max(0, index - 1));
        } else if (event.altKey && event.key === "ArrowDown") {
          event.preventDefault();
          moveBlock(index, Math.min(blocks.length - 1, index + 1));
        } else if (event.key === "Backspace" && !textarea.value && blocks.length > 1) {
          event.preventDefault();
          removeBlock(index);
        }
      };
      contentWrap.appendChild(textarea);
      requestAnimationFrame(() => autoResize(textarea));
    }

    shell.appendChild(contentWrap);
    return shell;
  }

  function render() {
    blocksRoot.replaceChildren();
    blocks.forEach((block, index) => {
      block.asset_url = block.asset_url || "";
      blocksRoot.appendChild(createBlock(block, index));
      const between = document.createElement("div");
      between.className = "kb-between";
      const add = document.createElement("button");
      add.type = "button";
      add.textContent = "+";
      add.title = "在这里插入内容";
      add.onclick = () => insertBlock(index + 1, "text");
      between.appendChild(add);
      blocksRoot.appendChild(between);
    });
  }

  function applyInsertRequest() {
    if (!insertRequest || Number(insertRequest.page_id) !== Number(page.id)) return;
    const nonce = String(insertRequest.nonce ?? "");
    const content = String(insertRequest.content ?? "").trim();
    if (!nonce || !content) return;
    const processedKey = `mynotebook:inserted:${page.id}:${nonce}`;
    try {
      if (sessionStorage.getItem(processedKey)) return;
      sessionStorage.setItem(processedKey, "1");
    } catch (_) {
      // The request is also consumed by Streamlit after this render.
    }
    blocks.push({
      uid: makeUid(),
      type: insertRequest.block_type === "text" ? "text" : "callout",
      content,
      asset_path: "",
      metadata: {
        source: "ai_tutor_draft",
        provider: String(insertRequest.provider ?? ""),
      },
    });
    activeIndex = blocks.length - 1;
    markDirty();
    saveStatus.textContent = "已插入 AI 建议草稿，等待自动保存";
  }

  titleInput.oninput = () => {
    title = titleInput.value;
    markDirty();
  };
  titleInput.onkeydown = (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      requestSave("手动保存");
    } else if (event.key === "Enter") {
      event.preventDefault();
      parentElement.querySelector(".kb-textarea, .kb-caption")?.focus();
    }
  };

  parentElement.querySelectorAll("[data-add]").forEach((button) => {
    button.onclick = () => insertBlock(Math.min(activeIndex + 1, blocks.length), button.dataset.add);
  });
  addLastButton.onclick = () => insertBlock(blocks.length, "text");
  imageButton.onclick = () => imageInput.click();
  imageInput.onchange = () => {
    readImages(imageInput.files ?? [], Math.min(activeIndex + 1, blocks.length));
    imageInput.value = "";
  };
  saveButton.onclick = () => requestSave("手动保存");

  const globalKeydown = (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      requestSave("手动保存");
    }
  };
  parentElement.addEventListener("keydown", globalKeydown);

  applyInsertRequest();
  render();
  if (dirty) persistDraft();
  restoreFocus();

  const saveBeforeLeaving = () => {
    if (!dirty) return;
    persistDraft();
    requestSave("离开页面自动保存");
  };
  const saveWhenHidden = () => {
    if (document.visibilityState === "hidden") saveBeforeLeaving();
  };
  globalThis.addEventListener("pagehide", saveBeforeLeaving);
  globalThis.addEventListener("beforeunload", saveBeforeLeaving);
  document.addEventListener("visibilitychange", saveWhenHidden);

  return () => {
    if (idleSaveTimer !== null) clearTimeout(idleSaveTimer);
    saveBeforeLeaving();
    parentElement.removeEventListener("keydown", globalKeydown);
    globalThis.removeEventListener("pagehide", saveBeforeLeaving);
    globalThis.removeEventListener("beforeunload", saveBeforeLeaving);
    document.removeEventListener("visibilitychange", saveWhenHidden);
  };
}
