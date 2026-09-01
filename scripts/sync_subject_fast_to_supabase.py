from __future__ import annotations

import argparse
import json
import mimetypes
import sqlite3
import sys
from pathlib import Path

import migrate_to_supabase as migration


ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Batch-merge one local subject into Supabase")
    parser.add_argument("subject")
    parser.add_argument(
        "--secrets",
        type=Path,
        default=ROOT / ".streamlit" / "cloud-secrets.toml",
    )
    parser.add_argument("--source", type=Path, default=ROOT / "my_study_data.db")
    args = parser.parse_args()

    migration.load_secrets(args.secrets.resolve())
    sys.path.insert(0, str(ROOT))
    from knowledge_base import db as cloud_db

    source = sqlite3.connect(args.source.resolve())
    source.row_factory = sqlite3.Row
    try:
        subject = source.execute(
            "SELECT * FROM kb_subjects WHERE name=?", (args.subject,)
        ).fetchone()
        if subject is None:
            raise SystemExit(f"Local subject not found: {args.subject}")
        pages = source.execute(
            "SELECT * FROM kb_pages WHERE subject_id=? ORDER BY id",
            (subject["id"],),
        ).fetchall()

        synced: list[int] = []
        inserted: list[int] = []
        skipped_newer: list[dict[str, object]] = []
        with cloud_db.transaction() as target:
            cloud_subject = target.execute(
                "SELECT * FROM kb_subjects WHERE name=?", (args.subject,)
            ).fetchone()
            if cloud_subject is None:
                collision = target.execute(
                    "SELECT name FROM kb_subjects WHERE id=?", (subject["id"],)
                ).fetchone()
                if collision:
                    raise RuntimeError(
                        f"Subject id collision with cloud subject: {collision['name']}"
                    )
                target.execute(
                    "INSERT INTO kb_subjects(id,name,description,archived,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        subject["id"],
                        subject["name"],
                        subject["description"],
                        subject["archived"],
                        subject["created_at"],
                        subject["updated_at"],
                    ),
                )
                subject_id = int(subject["id"])
            else:
                subject_id = int(cloud_subject["id"])
                if str(subject["updated_at"]) > str(cloud_subject["updated_at"]):
                    target.execute(
                        "UPDATE kb_subjects SET description=?,archived=?,updated_at=? WHERE id=?",
                        (
                            subject["description"],
                            subject["archived"],
                            subject["updated_at"],
                            subject_id,
                        ),
                    )

            cloud_pages = {
                int(row["id"]): row
                for row in target.execute(
                    "SELECT id,subject_id,title,updated_at FROM kb_pages"
                ).fetchall()
            }
            local_ids = {int(page["id"]) for page in pages}
            for page in pages:
                page_id = int(page["id"])
                cloud_page = cloud_pages.get(page_id)
                if cloud_page and int(cloud_page["subject_id"]) != subject_id:
                    raise RuntimeError(
                        f"Page id {page_id} collides with cloud page: {cloud_page['title']}"
                    )
                if cloud_page and str(cloud_page["updated_at"]) > str(page["updated_at"]):
                    skipped_newer.append(
                        {
                            "id": page_id,
                            "title": page["title"],
                            "local_updated": page["updated_at"],
                            "cloud_updated": cloud_page["updated_at"],
                        }
                    )
                    continue
                if cloud_page and str(cloud_page["updated_at"]) == str(page["updated_at"]):
                    continue

                if cloud_page:
                    target.execute(
                        "UPDATE kb_pages SET subject_id=?,parent_id=NULL,title=?,kind=?,"
                        "sort_order=?,source_type=?,source_ref=?,archived=?,created_at=?,updated_at=? "
                        "WHERE id=?",
                        (
                            subject_id,
                            page["title"],
                            page["kind"],
                            page["sort_order"],
                            page["source_type"],
                            page["source_ref"],
                            page["archived"],
                            page["created_at"],
                            page["updated_at"],
                            page_id,
                        ),
                    )
                else:
                    target.execute(
                        "INSERT INTO kb_pages(id,subject_id,parent_id,title,kind,sort_order,"
                        "source_type,source_ref,archived,created_at,updated_at) "
                        "VALUES (?,?,NULL,?,?,?,?,?,?,?,?)",
                        (
                            page_id,
                            subject_id,
                            page["title"],
                            page["kind"],
                            page["sort_order"],
                            page["source_type"],
                            page["source_ref"],
                            page["archived"],
                            page["created_at"],
                            page["updated_at"],
                        ),
                    )
                    inserted.append(page_id)

                target.execute("DELETE FROM kb_blocks WHERE page_id=?", (page_id,))
                blocks = source.execute(
                    "SELECT * FROM kb_blocks WHERE page_id=? ORDER BY sort_order,id",
                    (page_id,),
                ).fetchall()
                for block in blocks:
                    target.execute(
                        "INSERT INTO kb_blocks(uid,page_id,block_type,content,asset_path,"
                        "sort_order,metadata_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            block["uid"],
                            page_id,
                            block["block_type"],
                            block["content"],
                            migration.cloud_asset_path(str(block["asset_path"] or "")),
                            block["sort_order"],
                            block["metadata_json"],
                            block["created_at"],
                            block["updated_at"],
                        ),
                    )
                synced.append(page_id)

            skipped_ids = {int(item["id"]) for item in skipped_newer}
            for page in pages:
                page_id = int(page["id"])
                if page_id in skipped_ids:
                    continue
                parent_id = page["parent_id"]
                if parent_id is not None and int(parent_id) not in local_ids:
                    parent_id = None
                target.execute(
                    "UPDATE kb_pages SET parent_id=? WHERE id=? AND subject_id=?",
                    (parent_id, page_id, subject_id),
                )

            for page_id in synced:
                cloud_db._refresh_links(target, page_id)

            for table in ("kb_subjects", "kb_pages", "kb_blocks"):
                target.execute(
                    f"SELECT setval(pg_get_serial_sequence('{table}','id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table}),1), "
                    f"EXISTS(SELECT 1 FROM {table}))"
                )

            mismatches = []
            for page in pages:
                page_id = int(page["id"])
                if page_id in skipped_ids:
                    continue
                local_count = int(
                    source.execute(
                        "SELECT COUNT(*) FROM kb_blocks WHERE page_id=?", (page_id,)
                    ).fetchone()[0]
                )
                cloud_count = int(
                    target.execute(
                        "SELECT COUNT(*) AS count FROM kb_blocks WHERE page_id=?",
                        (page_id,),
                    ).fetchone()["count"]
                )
                if local_count != cloud_count:
                    mismatches.append(
                        {"page_id": page_id, "local": local_count, "cloud": cloud_count}
                    )
            if mismatches:
                raise RuntimeError(f"Block verification failed: {mismatches[:5]}")

        uploaded = 0
        asset_rows = source.execute(
            "SELECT DISTINCT b.asset_path FROM kb_blocks b JOIN kb_pages p ON p.id=b.page_id "
            "WHERE p.subject_id=? AND b.asset_path<>''",
            (subject["id"],),
        ).fetchall()
        for row in asset_rows:
            local_path = ROOT / "static" / str(row["asset_path"])
            if not local_path.exists():
                continue
            mime = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
            cloud_db._upload_asset(
                migration.cloud_asset_path(str(row["asset_path"])),
                local_path.read_bytes(),
                mime,
            )
            uploaded += 1

        print(
            json.dumps(
                {
                    "subject": args.subject,
                    "local_pages": len(pages),
                    "synced_pages": len(synced),
                    "inserted_pages": len(inserted),
                    "skipped_cloud_newer": skipped_newer,
                    "uploaded_assets": uploaded,
                    "block_count_mismatches": [],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        source.close()


if __name__ == "__main__":
    raise SystemExit(main())
