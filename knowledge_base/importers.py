from __future__ import annotations

import hashlib
import json
import posixpath
import re
import uuid
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from . import db


DOCX_SUBJECT_MAP = {
    "概率论": "概率论与数理统计",
    "复变函数": "复变函数",
}

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}
TAG_TEXT = f"{{{NS['w']}}}t"
TAG_TAB = f"{{{NS['w']}}}tab"
TAG_BREAK = f"{{{NS['w']}}}br"
TAG_BLIP = f"{{{NS['a']}}}blip"
ATTR_EMBED = f"{{{NS['r']}}}embed"


def run_initial_imports() -> dict[str, Any]:
    """Run additive, idempotent imports and return a short report."""
    report: dict[str, Any] = {}
    db.create_pre_migration_backup()
    if not db.migration_done("legacy-v1-to-pages"):
        report["legacy"] = import_legacy_database()
        db.mark_migration("legacy-v1-to-pages", report["legacy"])

    docx_results = []
    note_dir = db.ROOT / "学科笔记"
    for stem in ("概率论", "复变函数"):
        path = note_dir / f"{stem}.docx"
        if path.exists():
            result = import_docx_if_needed(path, DOCX_SUBJECT_MAP[stem])
            if result:
                docx_results.append(result)
    if docx_results:
        report["docx"] = docx_results
    if not db.migration_done("organize-imported-outlines-v1"):
        organization = organize_imported_outlines()
        db.mark_migration("organize-imported-outlines-v1", organization)
        report["organization"] = organization
    return report


def import_legacy_database() -> dict[str, int]:
    counts = {"subjects": 0, "pages": 0, "entries": 0, "reflections": 0}
    chapter_pages: dict[tuple[str, str], int] = {}

    categories = {
        row["category"]
        for row in db.query("SELECT DISTINCT category FROM entries WHERE category<>''")
        if row["category"]
    }
    categories.update(
        row["category"]
        for row in db.query("SELECT DISTINCT category FROM chapters WHERE category<>''")
        if row["category"]
    )
    categories.update(
        row["category"]
        for row in db.query("SELECT DISTINCT category FROM notes WHERE category<>''")
        if row["category"]
    )

    subject_ids: dict[str, int] = {}
    for name in sorted(categories):
        subject_ids[name] = db.ensure_subject(name)
        counts["subjects"] += 1

    for row in db.query(
        "SELECT category,title,chapter_order,summary FROM chapters "
        "ORDER BY category,chapter_order,title"
    ):
        category = row["category"] or "未分类"
        subject_id = subject_ids.setdefault(category, db.ensure_subject(category))
        page_id = db.create_page(
            subject_id,
            row["title"] or "未命名章节",
            kind="章节",
            source_type="legacy_chapter",
            source_ref=f"{category}|{row['title']}",
            blocks=(
                [{"type": "callout", "content": row["summary"]}]
                if row["summary"]
                else []
            ),
        )
        chapter_pages[(category, row["title"] or "")] = page_id
        counts["pages"] += 1

    def ensure_chapter(category: str, title: str) -> int | None:
        clean = (title or "").strip()
        if not clean:
            return None
        key = (category, clean)
        if key not in chapter_pages:
            subject_id = subject_ids.setdefault(category, db.ensure_subject(category))
            chapter_pages[key] = db.create_page(
                subject_id,
                clean,
                kind="章节",
                source_type="legacy_chapter",
                source_ref=f"{category}|{clean}",
            )
            counts["pages"] += 1
        return chapter_pages[key]

    entries = db.query(
        "SELECT id,type,category,title,content,date,image_path,topic FROM entries "
        "ORDER BY id"
    )
    for row in entries:
        category = row["category"] or "未分类"
        subject_id = subject_ids.setdefault(category, db.ensure_subject(category))
        parent_id = ensure_chapter(category, row["title"] or "")
        blocks: list[dict[str, Any]] = []
        if row["content"]:
            blocks.append({"type": "text", "content": row["content"]})
        for image_path in (row["image_path"] or "").split(","):
            image_path = image_path.strip()
            if image_path:
                copied = db.copy_legacy_asset(image_path, "legacy")
                if copied:
                    blocks.append(
                        {
                            "type": "image",
                            "content": "",
                            "asset_path": copied,
                            "metadata": {"legacy_path": image_path},
                        }
                    )
        title = (row["topic"] or row["title"] or f"旧笔记 {row['id']}").strip()
        page_id = db.create_page(
            subject_id,
            title,
            parent_id,
            kind=(row["type"] or "题型笔记"),
            source_type="legacy_entry",
            source_ref=str(row["id"]),
            blocks=blocks,
        )
        counts["entries"] += 1
        counts["pages"] += 1
        for review in db.query(
            "SELECT id,review_date,insight FROM reviews WHERE entry_id=? ORDER BY id",
            (row["id"],),
        ):
            db.add_reflection(
                page_id,
                review["insight"] or "",
                review["review_date"] or row["date"],
                source_ref=f"legacy_review:{review['id']}",
            )
            counts["reflections"] += 1

    for row in db.query(
        "SELECT id,category,chapter,content,date,image_path FROM notes ORDER BY id"
    ):
        category = row["category"] or "未分类"
        subject_id = subject_ids.setdefault(category, db.ensure_subject(category))
        parent_id = ensure_chapter(category, row["chapter"] or "")
        blocks = [{"type": "text", "content": row["content"] or ""}]
        for image_path in (row["image_path"] or "").split(","):
            copied = db.copy_legacy_asset(image_path.strip(), "legacy")
            if copied:
                blocks.append({"type": "image", "content": "", "asset_path": copied})
        db.create_page(
            subject_id,
            f"{row['date'] or '旧版'} 自由笔记",
            parent_id,
            kind="自由笔记",
            source_type="legacy_note",
            source_ref=str(row["id"]),
            blocks=blocks,
        )
        counts["pages"] += 1

    _import_outline_v2(counts)
    return counts


def _import_outline_v2(counts: dict[str, int]) -> None:
    chapter_map: dict[int, int] = {}
    section_map: dict[int, int] = {}
    for subject in db.query("SELECT id,name FROM subjects ORDER BY id"):
        subject_id = db.ensure_subject(subject["name"])
        counts["subjects"] += 1
        for chapter in db.query(
            "SELECT id,title,summary FROM outline_chapters "
            "WHERE subject_id=? ORDER BY order_idx,id",
            (subject["id"],),
        ):
            blocks = (
                [{"type": "callout", "content": chapter["summary"]}]
                if chapter["summary"]
                else []
            )
            page_id = db.create_page(
                subject_id,
                chapter["title"],
                kind="章节",
                source_type="outline_chapter",
                source_ref=str(chapter["id"]),
                blocks=blocks,
            )
            chapter_map[int(chapter["id"])] = page_id
            counts["pages"] += 1
            for section in db.query(
                "SELECT id,title,summary FROM outline_sections "
                "WHERE chapter_id=? ORDER BY order_idx,id",
                (chapter["id"],),
            ):
                section_blocks = (
                    [{"type": "callout", "content": section["summary"]}]
                    if section["summary"]
                    else []
                )
                section_page = db.create_page(
                    subject_id,
                    section["title"],
                    page_id,
                    kind="知识点",
                    source_type="outline_section",
                    source_ref=str(section["id"]),
                    blocks=section_blocks,
                )
                section_map[int(section["id"])] = section_page
                counts["pages"] += 1

    for item in db.query(
        "SELECT id,subject_id,chapter_id,section_id,item_type,title,content,"
        "image_path,date FROM study_items ORDER BY id"
    ):
        subject_row = db.query("SELECT name FROM subjects WHERE id=?", (item["subject_id"],))
        if not subject_row:
            continue
        subject_id = db.ensure_subject(subject_row[0]["name"])
        parent_id = (
            section_map.get(int(item["section_id"]))
            if item["section_id"] is not None
            else chapter_map.get(int(item["chapter_id"]))
        )
        blocks = [{"type": "text", "content": item["content"] or ""}]
        for image_path in (item["image_path"] or "").split(","):
            copied = db.copy_legacy_asset(image_path.strip(), "legacy-v2")
            if copied:
                blocks.append({"type": "image", "content": "", "asset_path": copied})
        page_id = db.create_page(
            subject_id,
            item["title"] or f"{item['item_type']} {item['id']}",
            parent_id,
            kind=item["item_type"],
            source_type="study_item",
            source_ref=str(item["id"]),
            blocks=blocks,
        )
        counts["pages"] += 1
        for review in db.query(
            "SELECT id,review_date,insight FROM study_item_reviews "
            "WHERE item_id=? ORDER BY id",
            (item["id"],),
        ):
            db.add_reflection(
                page_id,
                review["insight"] or "",
                review["review_date"] or item["date"],
                source_ref=f"study_review:{review['id']}",
            )
            counts["reflections"] += 1


def import_docx_if_needed(path: Path, subject_name: str) -> dict[str, Any] | None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    migration_key = f"docx:{path.name}:{digest}"
    if db.migration_done(migration_key):
        return None
    subject_id = db.ensure_subject(subject_name)
    blocks, image_count = parse_docx(path)
    page_id = db.create_page(
        subject_id,
        f"{path.stem} · Word 原稿",
        kind="Word 导入",
        source_type="docx",
        source_ref=f"{path.resolve()}|{digest}",
        blocks=blocks,
    )
    result = {
        "file": path.name,
        "page_id": page_id,
        "blocks": len(blocks),
        "images": image_count,
    }
    db.mark_migration(migration_key, result)
    return result


def parse_docx(path: Path) -> tuple[list[dict[str, Any]], int]:
    blocks: list[dict[str, Any]] = []
    image_count = 0
    with ZipFile(path) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
        relationships = _document_relationships(archive)
        body = document.find("w:body", NS)
        if body is None:
            return [], 0
        for paragraph in body.findall("w:p", NS):
            buffer: list[str] = []

            def flush_text() -> None:
                text = "".join(buffer).strip()
                buffer.clear()
                if text:
                    blocks.append(
                        {
                            "uid": uuid.uuid4().hex,
                            "type": _classify_paragraph(text),
                            "content": text,
                        }
                    )

            for node in paragraph.iter():
                if node.tag == TAG_TEXT:
                    buffer.append(node.text or "")
                elif node.tag == TAG_TAB:
                    buffer.append("\t")
                elif node.tag == TAG_BREAK:
                    buffer.append("\n")
                elif node.tag == TAG_BLIP:
                    rel_id = node.attrib.get(ATTR_EMBED)
                    target = relationships.get(rel_id or "")
                    if not target:
                        continue
                    flush_text()
                    member = posixpath.normpath(posixpath.join("word", target))
                    try:
                        raw = archive.read(member)
                    except KeyError:
                        continue
                    asset_path = _store_docx_image(raw, Path(member).suffix, path.stem)
                    blocks.append(
                        {
                            "uid": uuid.uuid4().hex,
                            "type": "image",
                            "content": "",
                            "asset_path": asset_path,
                            "metadata": {"source_docx": path.name},
                        }
                    )
                    image_count += 1
            flush_text()
    return blocks, image_count


def _document_relationships(archive: ZipFile) -> dict[str, str]:
    root = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
    return {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in root.findall("pr:Relationship", NS)
        if rel.attrib.get("Type", "").endswith("/image")
    }


def _store_docx_image(raw: bytes, extension: str, namespace: str) -> str:
    digest = hashlib.sha256(raw).hexdigest()
    clean_extension = extension.lower() if extension else ".png"
    relative = (
        Path("attachments")
        / "docx"
        / _safe_segment(namespace)
        / f"{digest}{clean_extension}"
    )
    target = db.STATIC_DIR / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(raw)
    return relative.as_posix()


def _safe_segment(value: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", value).strip(" .") or "docx"


def _classify_paragraph(text: str) -> str:
    compact = text.strip()
    if re.match(r"^第\s*[0-9一二三四五六七八九十]+\s*章", compact):
        return "heading1"
    if re.match(r"^\d{4}[./-]\d{1,2}[./-]\d{1,2}$", compact):
        return "heading1"
    if re.match(r"^[一二三四五六七八九十]+[.、．]\s*", compact):
        return "heading2"
    if len(compact) <= 60 and re.match(r"^\d+[.、．]\s*\S+", compact):
        return "heading3"
    return "text"


def organize_imported_outlines() -> dict[str, int]:
    """Fold legacy empty outline pages under one explicit, collapsible node."""
    organized = 0
    subjects = db.query(
        """
        SELECT DISTINCT s.id,s.name
        FROM kb_subjects s
        JOIN kb_pages p ON p.subject_id=s.id
        WHERE p.source_type='outline_chapter'
        """
    )
    for subject in subjects:
        group_id = db.create_page(
            int(subject["id"]),
            "课程大纲（旧版保留）",
            kind="旧版结构",
            source_type="outline_group",
            source_ref=str(subject["id"]),
            blocks=[
                {
                    "type": "callout",
                    "content": "这是旧版三级目录的无损保留。它不再限制新笔记的写法，可以按需参考或移动。",
                }
            ],
        )
        with db.transaction() as conn:
            conn.execute(
                """
                UPDATE kb_pages SET parent_id=?,updated_at=?
                WHERE subject_id=? AND source_type='outline_chapter'
                """,
                (group_id, db.now(), subject["id"]),
            )
            conn.execute(
                """
                UPDATE kb_pages SET sort_order=0
                WHERE subject_id=? AND source_type='docx'
                """,
                (subject["id"],),
            )
            conn.execute(
                "UPDATE kb_pages SET sort_order=1 WHERE id=?",
                (group_id,),
            )
        organized += 1
    return {"subjects": organized}
