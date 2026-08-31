from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TABLES = [
    "kb_subjects",
    "kb_pages",
    "kb_blocks",
    "kb_reflections",
    "kb_versions",
    "kb_tags",
    "kb_page_tags",
    "kb_links",
    "kb_migrations",
]
SERIAL_TABLES = [
    "kb_subjects",
    "kb_pages",
    "kb_blocks",
    "kb_reflections",
    "kb_versions",
    "kb_tags",
]


def load_secrets(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"Secrets file not found: {path}")
    import tomllib

    values = tomllib.loads(path.read_text(encoding="utf-8"))
    for key, value in values.items():
        if isinstance(value, (str, int, float, bool)) and str(value):
            os.environ.setdefault(str(key), str(value))


def source_rows(connection: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return connection.execute(f"SELECT * FROM {table}").fetchall()


def cloud_asset_path(asset_path: str) -> str:
    if not asset_path:
        return ""
    suffix = Path(asset_path).suffix.lower()
    digest = hashlib.sha256(asset_path.encode("utf-8")).hexdigest()
    return f"attachments/cloud/{digest}{suffix}"


def cloud_blocks_json(value: str) -> str:
    try:
        blocks = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value
    if not isinstance(blocks, list):
        return value
    for block in blocks:
        if isinstance(block, dict) and block.get("asset_path"):
            block["asset_path"] = cloud_asset_path(str(block["asset_path"]))
    return json.dumps(blocks, ensure_ascii=False, sort_keys=True)


def insert_rows(cloud_db, table: str, rows: list[sqlite3.Row]) -> int:
    if not rows:
        return 0
    columns = list(rows[0].keys())
    placeholders = ",".join("?" for _ in columns)
    sql = (
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders}) "
        "ON CONFLICT DO NOTHING"
    )
    with cloud_db.transaction() as target:
        for row in rows:
            values = [row[column] for column in columns]
            if table == "kb_pages":
                # A child page can have a lower id than its parent in the local
                # notebook. Insert every page first, then restore the tree.
                values[columns.index("parent_id")] = None
            elif table == "kb_blocks":
                asset_index = columns.index("asset_path")
                values[asset_index] = cloud_asset_path(str(values[asset_index] or ""))
            elif table == "kb_versions":
                json_index = columns.index("blocks_json")
                values[json_index] = cloud_blocks_json(str(values[json_index] or "[]"))
            target.execute(sql, values)
        if table == "kb_pages":
            for row in rows:
                if row["parent_id"] is not None:
                    target.execute(
                        "UPDATE kb_pages SET parent_id=? WHERE id=?",
                        (row["parent_id"], row["id"]),
                    )
    return len(rows)


def upload_assets(cloud_db, source: sqlite3.Connection) -> tuple[int, list[str]]:
    paths = [
        str(row[0])
        for row in source.execute(
            "SELECT DISTINCT asset_path FROM kb_blocks WHERE asset_path<>''"
        ).fetchall()
    ]
    uploaded = 0
    missing: list[str] = []
    for index, asset_path in enumerate(paths, start=1):
        local = ROOT / "static" / asset_path
        if not local.exists():
            missing.append(asset_path)
            continue
        mime = mimetypes.guess_type(local.name)[0] or "application/octet-stream"
        cloud_db._upload_asset(cloud_asset_path(asset_path), local.read_bytes(), mime)
        uploaded += 1
        if index % 25 == 0:
            print(f"assets: {index}/{len(paths)}", flush=True)
    return uploaded, missing


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate MyNotebook SQLite data to Supabase")
    parser.add_argument(
        "--secrets",
        type=Path,
        default=ROOT / ".streamlit" / "cloud-secrets.toml",
    )
    parser.add_argument("--source", type=Path, default=ROOT / "my_study_data.db")
    parser.add_argument(
        "--replace-partial",
        action="store_true",
        help="Clear only MyNotebook kb_* cloud tables before retrying a failed migration",
    )
    args = parser.parse_args()
    load_secrets(args.secrets.resolve())
    if not os.environ.get("SUPABASE_DB_URL"):
        raise SystemExit("SUPABASE_DB_URL is missing")

    sys.path.insert(0, str(ROOT))
    from knowledge_base import db as cloud_db

    if not cloud_db.CLOUD_MODE:
        raise SystemExit("Cloud mode did not activate")
    cloud_db.init_schema()
    if cloud_db.query("SELECT 1 FROM kb_subjects LIMIT 1"):
        if not args.replace_partial:
            raise SystemExit(
                "Cloud database is not empty; migration stopped without changing it"
            )
        with cloud_db.transaction() as target:
            target.execute(
                "TRUNCATE TABLE kb_links,kb_page_tags,kb_tags,kb_versions,"
                "kb_reflections,kb_blocks,kb_pages,kb_subjects,kb_migrations "
                "RESTART IDENTITY CASCADE"
            )
        print("Cleared partial MyNotebook cloud migration.", flush=True)

    source = sqlite3.connect(args.source.resolve())
    source.row_factory = sqlite3.Row
    counts: dict[str, int] = {}
    try:
        for table in TABLES:
            rows = source_rows(source, table)
            counts[table] = insert_rows(cloud_db, table, rows)
            print(f"{table}: {counts[table]}", flush=True)
        with cloud_db.transaction() as target:
            for table in SERIAL_TABLES:
                target.execute(
                    f"SELECT setval(pg_get_serial_sequence('{table}','id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table}),1), "
                    f"EXISTS(SELECT 1 FROM {table}))"
                )
        uploaded, missing = upload_assets(cloud_db, source)
    finally:
        source.close()

    verification = {
        table: int(cloud_db.query(f"SELECT COUNT(*) AS count FROM {table}")[0]["count"])
        for table in TABLES
    }
    report = {
        "source_counts": counts,
        "cloud_counts": verification,
        "uploaded_assets": uploaded,
        "missing_assets": missing,
    }
    report_path = ROOT / "backups" / "cloud-migration-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if counts != verification or missing:
        raise SystemExit("Migration verification failed; inspect cloud-migration-report.json")
    print("Migration verified successfully.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
