from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import streamlit as st


HERE = Path(__file__).resolve().parent


def _read(name: str) -> str:
    return (HERE / name).read_text(encoding="utf-8")


_EDITOR = st.components.v2.component(
    "mynotebook_block_editor_v12",
    html=_read("editor.html"),
    css=_read("editor.css"),
    js=_read("editor.js"),
    isolate_styles=True,
)


def block_editor(
    page: dict[str, Any],
    *,
    key: str,
    on_save: Callable[[], None],
    insert_request: dict[str, Any] | None = None,
):
    return _EDITOR(
        data={"page": page, "insert_request": insert_request},
        key=key,
        on_save_change=on_save,
        width="stretch",
        height="content",
    )
