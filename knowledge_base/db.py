from __future__ import annotations

import base64
import functools
import hashlib
import io
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(
    os.environ.get("MYNOTEBOOK_ROOT", Path(__file__).resolve().parent.parent)
).resolve()
DB_PATH = Path(os.environ.get("MYNOTEBOOK_DB_PATH", ROOT / "my_study_data.db")).resolve()
DATABASE_URL = os.environ.get("MYNOTEBOOK_DATABASE_URL") or os.environ.get(
    "SUPABASE_DB_URL", ""
)
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get(
    "SUPABASE_SECRET_KEY", ""
)
SUPABASE_BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "mynotebook-assets")
CLOUD_MODE = bool(DATABASE_URL)
STATIC_DIR = ROOT / "static"
ATTACHMENTS_DIR = STATIC_DIR / "attachments"
BACKUPS_DIR = ROOT / "backups"

_LOCK = threading.RLock()
_ALLOWED_BLOCK_TYPES = {
    "text",
    "heading1",
    "heading2",
    "heading3",
    "image",
    "equation",
    "callout",
    "divider",
}
_LINK_RE = re.compile(r"\[\[([^\[\]\n]+)\]\]")


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class _HybridRow(dict[str, Any]):
    def __init__(self, names: list[str], values: tuple[Any, ...]):
        super().__init__(zip(names, values))
        self._values = values

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


class _PgCursor:
    def __init__(self, cursor: Any):
        self._cursor = cursor
        self.lastrowid = None

    def _convert(self, row: Any) -> _HybridRow | None:
        if row is None:
            return None
        names = [str(column.name) for column in self._cursor.description]
        return _HybridRow(names, tuple(row))

    def fetchone(self) -> _HybridRow | None:
        return self._convert(self._cursor.fetchone())

    def fetchall(self) -> list[_HybridRow]:
        return [self._convert(row) for row in self._cursor.fetchall()]  # type: ignore[misc]

    def __iter__(self):
        return iter(self.fetchall())


class _PgConnection:
    def __init__(self, connection: Any):
        self._connection = connection

    def execute(self, sql: str, params: Iterable[Any] = ()) -> _PgCursor:
        cursor = self._connection.execute(_postgres_sql(sql), tuple(params))
        return _PgCursor(cursor)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


def _postgres_sql(sql: str) -> str:
    value = sql.replace(" COLLATE NOCASE", "")
    value = value.replace("char(10)", "chr(10)")
    if re.search(r"INSERT\s+OR\s+REPLACE\s+INTO\s+kb_migrations", value, re.I):
        value = re.sub(
            r"INSERT\s+OR\s+REPLACE\s+INTO\s+kb_migrations\s+VALUES\s*\(\?,\?,\?\)",
            "INSERT INTO kb_migrations(migration_key,completed_at,details_json) "
            "VALUES (?,?,?) ON CONFLICT(migration_key) DO UPDATE SET "
            "completed_at=EXCLUDED.completed_at,details_json=EXCLUDED.details_json",
            value,
            flags=re.I,
        )
    elif re.search(r"INSERT\s+OR\s+IGNORE\s+INTO", value, re.I):
        value = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", value, flags=re.I)
        value = value.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return value.replace("?", "%s")


def _connect(path: Path = DB_PATH) -> Any:
    if CLOUD_MODE:
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError(
                "云端模式需要 psycopg；请安装 requirements.txt 中的依赖。"
            ) from exc
        return _PgConnection(psycopg.connect(DATABASE_URL))
    conn = sqlite3.connect(path, timeout=60, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def transaction():
    with _LOCK:
        conn = _connect()
        try:
            if not CLOUD_MODE:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_schema() -> None:
    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    if CLOUD_MODE:
        _init_postgres_schema()
        return
    schema = """
    CREATE TABLE IF NOT EXISTS kb_subjects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        description TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS kb_pages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_id INTEGER NOT NULL REFERENCES kb_subjects(id) ON DELETE CASCADE,
        parent_id INTEGER REFERENCES kb_pages(id) ON DELETE SET NULL,
        title TEXT NOT NULL,
        kind TEXT DEFAULT '',
        sort_order REAL NOT NULL DEFAULT 0,
        source_type TEXT DEFAULT '',
        source_ref TEXT DEFAULT '',
        archived INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE UNIQUE INDEX IF NOT EXISTS kb_pages_source_uq
        ON kb_pages(source_type, source_ref)
        WHERE source_type <> '' AND source_ref <> '';
    CREATE INDEX IF NOT EXISTS kb_pages_subject_idx
        ON kb_pages(subject_id, parent_id, archived, sort_order);

    CREATE TABLE IF NOT EXISTS kb_blocks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid TEXT NOT NULL UNIQUE,
        page_id INTEGER NOT NULL REFERENCES kb_pages(id) ON DELETE CASCADE,
        block_type TEXT NOT NULL,
        content TEXT DEFAULT '',
        asset_path TEXT DEFAULT '',
        sort_order REAL NOT NULL,
        metadata_json TEXT DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS kb_blocks_page_idx
        ON kb_blocks(page_id, sort_order);

    CREATE TABLE IF NOT EXISTS kb_reflections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        page_id INTEGER NOT NULL REFERENCES kb_pages(id) ON DELETE CASCADE,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        source_ref TEXT DEFAULT '' UNIQUE
    );
    CREATE TABLE IF NOT EXISTS kb_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        page_id INTEGER NOT NULL REFERENCES kb_pages(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        blocks_json TEXT NOT NULL,
        reason TEXT DEFAULT '',
        saved_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS kb_versions_page_idx
        ON kb_versions(page_id, id DESC);

    CREATE TABLE IF NOT EXISTS kb_tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE
    );
    CREATE TABLE IF NOT EXISTS kb_page_tags (
        page_id INTEGER NOT NULL REFERENCES kb_pages(id) ON DELETE CASCADE,
        tag_id INTEGER NOT NULL REFERENCES kb_tags(id) ON DELETE CASCADE,
        PRIMARY KEY (page_id, tag_id)
    );
    CREATE TABLE IF NOT EXISTS kb_links (
        source_page_id INTEGER NOT NULL REFERENCES kb_pages(id) ON DELETE CASCADE,
        target_page_id INTEGER NOT NULL REFERENCES kb_pages(id) ON DELETE CASCADE,
        link_text TEXT NOT NULL,
        PRIMARY KEY (source_page_id, target_page_id, link_text)
    );
    CREATE TABLE IF NOT EXISTS kb_migrations (
        migration_key TEXT PRIMARY KEY,
        completed_at TEXT NOT NULL,
        details_json TEXT DEFAULT '{}'
    );
    """
    with transaction() as conn:
        conn.executescript(schema)
        subject_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(kb_subjects)")
        }
        if "archived" not in subject_columns:
            conn.execute(
                "ALTER TABLE kb_subjects "
                "ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"
            )


def _init_postgres_schema() -> None:
    statements = [
        """CREATE TABLE IF NOT EXISTS kb_subjects (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT '',
            archived SMALLINT NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS kb_pages (
            id BIGSERIAL PRIMARY KEY,
            subject_id BIGINT NOT NULL REFERENCES kb_subjects(id) ON DELETE CASCADE,
            parent_id BIGINT REFERENCES kb_pages(id) ON DELETE SET NULL,
            title TEXT NOT NULL,
            kind TEXT DEFAULT '',
            sort_order DOUBLE PRECISION NOT NULL DEFAULT 0,
            source_type TEXT DEFAULT '',
            source_ref TEXT DEFAULT '',
            archived SMALLINT NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        """CREATE UNIQUE INDEX IF NOT EXISTS kb_pages_source_uq
            ON kb_pages(source_type, source_ref)
            WHERE source_type <> '' AND source_ref <> ''""",
        """CREATE INDEX IF NOT EXISTS kb_pages_subject_idx
            ON kb_pages(subject_id, parent_id, archived, sort_order)""",
        """CREATE TABLE IF NOT EXISTS kb_blocks (
            id BIGSERIAL PRIMARY KEY,
            uid TEXT NOT NULL UNIQUE,
            page_id BIGINT NOT NULL REFERENCES kb_pages(id) ON DELETE CASCADE,
            block_type TEXT NOT NULL,
            content TEXT DEFAULT '',
            asset_path TEXT DEFAULT '',
            sort_order DOUBLE PRECISION NOT NULL,
            metadata_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        """CREATE INDEX IF NOT EXISTS kb_blocks_page_idx
            ON kb_blocks(page_id, sort_order)""",
        """CREATE TABLE IF NOT EXISTS kb_reflections (
            id BIGSERIAL PRIMARY KEY,
            page_id BIGINT NOT NULL REFERENCES kb_pages(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            source_ref TEXT DEFAULT '' UNIQUE
        )""",
        """CREATE TABLE IF NOT EXISTS kb_versions (
            id BIGSERIAL PRIMARY KEY,
            page_id BIGINT NOT NULL REFERENCES kb_pages(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            blocks_json TEXT NOT NULL,
            reason TEXT DEFAULT '',
            saved_at TEXT NOT NULL
        )""",
        """CREATE INDEX IF NOT EXISTS kb_versions_page_idx
            ON kb_versions(page_id, id DESC)""",
        """CREATE TABLE IF NOT EXISTS kb_tags (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE
        )""",
        """CREATE TABLE IF NOT EXISTS kb_page_tags (
            page_id BIGINT NOT NULL REFERENCES kb_pages(id) ON DELETE CASCADE,
            tag_id BIGINT NOT NULL REFERENCES kb_tags(id) ON DELETE CASCADE,
            PRIMARY KEY (page_id, tag_id)
        )""",
        """CREATE TABLE IF NOT EXISTS kb_links (
            source_page_id BIGINT NOT NULL REFERENCES kb_pages(id) ON DELETE CASCADE,
            target_page_id BIGINT NOT NULL REFERENCES kb_pages(id) ON DELETE CASCADE,
            link_text TEXT NOT NULL,
            PRIMARY KEY (source_page_id, target_page_id, link_text)
        )""",
        """CREATE TABLE IF NOT EXISTS kb_migrations (
            migration_key TEXT PRIMARY KEY,
            completed_at TEXT NOT NULL,
            details_json TEXT DEFAULT '{}'
        )""",
        "ALTER TABLE kb_subjects ADD COLUMN IF NOT EXISTS archived SMALLINT NOT NULL DEFAULT 0",
    ]
    with transaction() as conn:
        for statement in statements:
            conn.execute(statement)
    ensure_storage_bucket()


def query(sql: str, params: Iterable[Any] = ()) -> list[Any]:
    with _LOCK:
        conn = _connect()
        try:
            return conn.execute(sql, tuple(params)).fetchall()
        finally:
            conn.close()


def migration_done(key: str) -> bool:
    return bool(query("SELECT 1 FROM kb_migrations WHERE migration_key=?", (key,)))


def mark_migration(key: str, details: dict[str, Any]) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO kb_migrations VALUES (?,?,?)",
            (key, now(), json.dumps(details, ensure_ascii=False)),
        )


def create_pre_migration_backup(label: str = "before_v2") -> Path | None:
    if CLOUD_MODE:
        return None
    marker = f"backup:{label}"
    if migration_done(marker):
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = BACKUPS_DIR / f"my_study_data_{label}_{stamp}.db"
    with _LOCK:
        source = _connect()
        dest = sqlite3.connect(target)
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()
    mark_migration(marker, {"path": str(target.relative_to(ROOT))})
    return target


def create_full_backup() -> tuple[bytes, str]:
    if CLOUD_MODE:
        return _create_cloud_backup()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    db_copy = BACKUPS_DIR / f".backup-{uuid.uuid4().hex}.db"
    with _LOCK:
        source = _connect()
        dest = sqlite3.connect(db_copy)
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()
    output = io.BytesIO()
    try:
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(db_copy, "my_study_data.db")
            if ATTACHMENTS_DIR.exists():
                for path in ATTACHMENTS_DIR.rglob("*"):
                    if path.is_file():
                        zf.write(path, Path("static") / path.relative_to(STATIC_DIR))
            zf.writestr(
                "README.txt",
                "MyNotebook 完整备份：数据库 + static/attachments。\n"
                "恢复前请先关闭知识库，并保留当前文件的副本。\n",
            )
    finally:
        db_copy.unlink(missing_ok=True)
    return output.getvalue(), f"MyNotebook-backup-{stamp}.zip"


def _create_cloud_backup() -> tuple[bytes, str]:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    tables = [
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
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for table in tables:
            rows = [dict(row) for row in query(f"SELECT * FROM {table}")]
            zf.writestr(
                f"database/{table}.json",
                json.dumps(rows, ensure_ascii=False, indent=2, default=str),
            )
        asset_paths = {
            str(row["asset_path"])
            for row in query(
                "SELECT DISTINCT asset_path FROM kb_blocks WHERE asset_path<>''"
            )
            if row["asset_path"]
        }
        for asset_path in sorted(asset_paths):
            raw = _read_asset(asset_path)
            if raw is not None:
                zf.writestr(f"static/{asset_path}", raw)
        zf.writestr(
            "README.txt",
            "MyNotebook 云端完整备份：Postgres 表 JSON + 全部引用附件。\n",
        )
    return output.getvalue(), f"MyNotebook-cloud-backup-{stamp}.zip"


def ensure_subject(name: str, description: str = "") -> int:
    clean = (name or "未命名学科").strip()
    timestamp = now()
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO kb_subjects(name, description, created_at, updated_at)
            VALUES (?,?,?,?)
            ON CONFLICT(name) DO UPDATE SET updated_at=excluded.updated_at
            """,
            (clean, description, timestamp, timestamp),
        )
        return int(
            conn.execute(
                "SELECT id FROM kb_subjects WHERE name=? COLLATE NOCASE", (clean,)
            ).fetchone()[0]
        )


def list_subjects(archived: bool | None = False) -> list[dict[str, Any]]:
    where = ""
    params: list[Any] = []
    if archived is not None:
        where = "WHERE s.archived=?"
        params.append(1 if archived else 0)
    rows = query(
        f"""
        SELECT s.*, COUNT(p.id) AS page_count
        FROM kb_subjects s
        LEFT JOIN kb_pages p ON p.subject_id=s.id AND p.archived=0
        {where}
        GROUP BY s.id ORDER BY s.name COLLATE NOCASE
        """,
        params,
    )
    return [dict(row) for row in rows]


def archive_subject(subject_id: int, archived: bool = True) -> None:
    """Hide or restore a whole subject without changing any of its pages."""
    with transaction() as conn:
        conn.execute(
            "UPDATE kb_subjects SET archived=?,updated_at=? WHERE id=?",
            (1 if archived else 0, now(), subject_id),
        )


def _next_page_order(conn: Any, subject_id: int, parent_id: int | None) -> float:
    if parent_id is None:
        row = conn.execute(
            "SELECT COALESCE(MAX(sort_order),-1)+1 FROM kb_pages "
            "WHERE subject_id=? AND parent_id IS NULL",
            (subject_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COALESCE(MAX(sort_order),-1)+1 FROM kb_pages "
            "WHERE subject_id=? AND parent_id=?",
            (subject_id, parent_id),
        ).fetchone()
    return float(row[0])


def create_page(
    subject_id: int,
    title: str,
    parent_id: int | None = None,
    *,
    kind: str = "",
    source_type: str = "",
    source_ref: str = "",
    blocks: list[dict[str, Any]] | None = None,
) -> int:
    timestamp = now()
    with transaction() as conn:
        if source_type and source_ref:
            existing = conn.execute(
                "SELECT id FROM kb_pages WHERE source_type=? AND source_ref=?",
                (source_type, source_ref),
            ).fetchone()
            if existing:
                return int(existing[0])
        order = _next_page_order(conn, subject_id, parent_id)
        insert_sql = """
            INSERT INTO kb_pages(
                subject_id,parent_id,title,kind,sort_order,source_type,source_ref,
                archived,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """
        if CLOUD_MODE:
            insert_sql += " RETURNING id"
        cur = conn.execute(
            insert_sql,
            (
                subject_id,
                parent_id,
                title.strip() or "未命名页面",
                kind,
                order,
                source_type,
                source_ref,
                0,
                timestamp,
                timestamp,
            ),
        )
        page_id = int(cur.fetchone()["id"] if CLOUD_MODE else cur.lastrowid)
        for index, block in enumerate(_normalize_blocks(blocks or [])):
            conn.execute(
                """
                INSERT INTO kb_blocks(
                    uid,page_id,block_type,content,asset_path,sort_order,
                    metadata_json,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    block["uid"],
                    page_id,
                    block["type"],
                    block["content"],
                    block["asset_path"],
                    index,
                    json.dumps(block["metadata"], ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )
        _refresh_links(conn, page_id)
        return page_id


def list_pages(
    subject_id: int | None = None,
    archived: bool = False,
    *,
    include_archived_subjects: bool = False,
) -> list[dict[str, Any]]:
    where = ["p.archived=?"]
    params: list[Any] = [1 if archived else 0]
    if subject_id is not None:
        where.append("p.subject_id=?")
        params.append(subject_id)
    elif not include_archived_subjects:
        where.append("s.archived=0")
    rows = query(
        f"""
        SELECT p.*, s.name AS subject_name,
               (SELECT COUNT(*) FROM kb_blocks b WHERE b.page_id=p.id) AS block_count
        FROM kb_pages p JOIN kb_subjects s ON s.id=p.subject_id
        WHERE {' AND '.join(where)}
        ORDER BY s.name COLLATE NOCASE, p.parent_id, p.sort_order, p.id
        """,
        params,
    )
    return [dict(row) for row in rows]


def _storage_headers(content_type: str | None = None) -> dict[str, str]:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise RuntimeError("云端附件需要 SUPABASE_URL 和 SUPABASE_SERVICE_ROLE_KEY。")
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _storage_request(
    method: str,
    endpoint: str,
    *,
    data: bytes | None = None,
    content_type: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> bytes:
    request = urllib.request.Request(
        f"{SUPABASE_URL}/storage/v1{endpoint}",
        data=data,
        method=method,
        headers={**_storage_headers(content_type), **(extra_headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Supabase Storage 请求失败（{exc.code}）：{detail}") from exc


def ensure_storage_bucket() -> None:
    if not CLOUD_MODE:
        return
    payload = json.dumps(
        {"id": SUPABASE_BUCKET, "name": SUPABASE_BUCKET, "public": False}
    ).encode("utf-8")
    try:
        _storage_request("POST", "/bucket", data=payload, content_type="application/json")
    except RuntimeError as exc:
        if "already exists" not in str(exc).lower() and "duplicate" not in str(exc).lower():
            raise


def _upload_asset(asset_path: str, raw: bytes, mime: str) -> None:
    encoded_path = urllib.parse.quote(asset_path.replace("\\", "/"), safe="/")
    _storage_request(
        "POST",
        f"/object/{urllib.parse.quote(SUPABASE_BUCKET)}/{encoded_path}",
        data=raw,
        content_type=mime,
        extra_headers={"x-upsert": "true"},
    )


def _read_asset(asset_path: str) -> bytes | None:
    if not CLOUD_MODE:
        target = STATIC_DIR / asset_path
        return target.read_bytes() if target.exists() else None
    encoded_path = urllib.parse.quote(asset_path.replace("\\", "/"), safe="/")
    try:
        return _storage_request(
            "GET",
            f"/object/authenticated/{urllib.parse.quote(SUPABASE_BUCKET)}/{encoded_path}",
        )
    except RuntimeError:
        return None


@functools.lru_cache(maxsize=4096)
def _signed_asset_url_cached(asset_path: str, time_bucket: int) -> str:
    del time_bucket
    encoded_path = urllib.parse.quote(asset_path.replace("\\", "/"), safe="/")
    payload = json.dumps({"expiresIn": 3600}).encode("utf-8")
    raw = _storage_request(
        "POST",
        f"/object/sign/{urllib.parse.quote(SUPABASE_BUCKET)}/{encoded_path}",
        data=payload,
        content_type="application/json",
    )
    signed = str(json.loads(raw.decode("utf-8")).get("signedURL") or "")
    if not signed:
        return ""
    if signed.startswith("http"):
        return signed
    if signed.startswith("/storage/v1"):
        return f"{SUPABASE_URL}{signed}"
    return f"{SUPABASE_URL}/storage/v1{signed if signed.startswith('/') else '/' + signed}"


def asset_url(asset_path: str) -> str:
    if not asset_path:
        return ""
    if not CLOUD_MODE:
        return f"app/static/{asset_path.replace(chr(92), '/')}"
    return _signed_asset_url_cached(asset_path, int(time.time() // 3000))


def get_page(page_id: int) -> dict[str, Any] | None:
    rows = query(
        """
        SELECT p.*, s.name AS subject_name, s.archived AS subject_archived
        FROM kb_pages p JOIN kb_subjects s ON s.id=p.subject_id
        WHERE p.id=?
        """,
        (page_id,),
    )
    if not rows:
        return None
    page = dict(rows[0])
    block_rows = query(
        "SELECT * FROM kb_blocks WHERE page_id=? ORDER BY sort_order,id", (page_id,)
    )
    page["blocks"] = [
        {
            "uid": row["uid"],
            "type": row["block_type"],
            "content": row["content"] or "",
            "asset_path": row["asset_path"] or "",
            "asset_url": asset_url(str(row["asset_path"] or "")),
            "metadata": _json_object(row["metadata_json"]),
        }
        for row in block_rows
    ]
    page["tags"] = [
        row["name"]
        for row in query(
            """
            SELECT t.name FROM kb_tags t JOIN kb_page_tags pt ON pt.tag_id=t.id
            WHERE pt.page_id=? ORDER BY t.name
            """,
            (page_id,),
        )
    ]
    page["reflections"] = [
        dict(row)
        for row in query(
            "SELECT * FROM kb_reflections WHERE page_id=? ORDER BY created_at,id",
            (page_id,),
        )
    ]
    return page


def _json_object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _normalize_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in blocks:
        block_type = str(raw.get("type") or "text")
        if block_type not in _ALLOWED_BLOCK_TYPES:
            block_type = "text"
        uid = str(raw.get("uid") or uuid.uuid4().hex)
        if uid in seen:
            uid = uuid.uuid4().hex
        seen.add(uid)
        content = str(raw.get("content") or "")
        asset_path = str(raw.get("asset_path") or "")
        if block_type == "image" and asset_path.startswith("data:image/"):
            asset_path = save_data_image(asset_path)
        metadata = raw.get("metadata")
        normalized.append(
            {
                "uid": uid,
                "type": block_type,
                "content": content,
                "asset_path": asset_path,
                "metadata": metadata if isinstance(metadata, dict) else {},
            }
        )
    if not normalized:
        normalized.append(
            {
                "uid": uuid.uuid4().hex,
                "type": "text",
                "content": "",
                "asset_path": "",
                "metadata": {},
            }
        )
    return normalized


def save_data_image(data_url: str) -> str:
    match = re.match(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$", data_url, re.S)
    if not match:
        raise ValueError("无法识别粘贴的图片")
    mime, payload = match.groups()
    raw = base64.b64decode(payload, validate=True)
    if len(raw) > 25 * 1024 * 1024:
        raise ValueError("单张图片不能超过 25 MB")
    extension = mimetypes.guess_extension(mime) or ".png"
    if extension == ".jpe":
        extension = ".jpg"
    digest = hashlib.sha256(raw).hexdigest()
    relative = Path("attachments") / "pasted" / f"{digest}{extension}"
    if CLOUD_MODE:
        _upload_asset(relative.as_posix(), raw, mime)
        return relative.as_posix()
    target = STATIC_DIR / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(raw)
    return relative.as_posix()


def _page_payload(conn: Any, page_id: int) -> tuple[str, str]:
    page = conn.execute("SELECT title FROM kb_pages WHERE id=?", (page_id,)).fetchone()
    rows = conn.execute(
        "SELECT uid,block_type,content,asset_path,metadata_json "
        "FROM kb_blocks WHERE page_id=? ORDER BY sort_order,id",
        (page_id,),
    ).fetchall()
    blocks = [
        {
            "uid": row["uid"],
            "type": row["block_type"],
            "content": row["content"] or "",
            "asset_path": row["asset_path"] or "",
            "metadata": _json_object(row["metadata_json"]),
        }
        for row in rows
    ]
    return str(page["title"]), json.dumps(blocks, ensure_ascii=False, sort_keys=True)


def save_page(
    page_id: int,
    title: str,
    blocks: list[dict[str, Any]],
    *,
    reason: str = "自动保存",
) -> bool:
    normalized = _normalize_blocks(blocks)
    timestamp = now()
    clean_title = title.strip() or "未命名页面"
    with transaction() as conn:
        old_title, old_json = _page_payload(conn, page_id)
        new_json = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
        if old_title == clean_title and old_json == new_json:
            return False
        conn.execute(
            "INSERT INTO kb_versions(page_id,title,blocks_json,reason,saved_at) "
            "VALUES (?,?,?,?,?)",
            (page_id, old_title, old_json, reason, timestamp),
        )
        conn.execute(
            "UPDATE kb_pages SET title=?,updated_at=? WHERE id=?",
            (clean_title, timestamp, page_id),
        )
        existing = {
            row["uid"]: int(row["id"])
            for row in conn.execute(
                "SELECT id,uid FROM kb_blocks WHERE page_id=?", (page_id,)
            ).fetchall()
        }
        retained: set[str] = set()
        for index, block in enumerate(normalized):
            retained.add(block["uid"])
            values = (
                block["type"],
                block["content"],
                block["asset_path"],
                float(index),
                json.dumps(block["metadata"], ensure_ascii=False),
                timestamp,
            )
            if block["uid"] in existing:
                conn.execute(
                    """
                    UPDATE kb_blocks SET block_type=?,content=?,asset_path=?,
                        sort_order=?,metadata_json=?,updated_at=?
                    WHERE id=?
                    """,
                    values + (existing[block["uid"]],),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO kb_blocks(
                        uid,page_id,block_type,content,asset_path,sort_order,
                        metadata_json,created_at,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        block["uid"],
                        page_id,
                        block["type"],
                        block["content"],
                        block["asset_path"],
                        float(index),
                        json.dumps(block["metadata"], ensure_ascii=False),
                        timestamp,
                        timestamp,
                    ),
                )
        for uid, block_id in existing.items():
            if uid not in retained:
                conn.execute("DELETE FROM kb_blocks WHERE id=?", (block_id,))
        conn.execute(
            """
            DELETE FROM kb_versions WHERE page_id=? AND id NOT IN (
                SELECT id FROM kb_versions WHERE page_id=? ORDER BY id DESC LIMIT 80
            )
            """,
            (page_id, page_id),
        )
        _refresh_links(conn, page_id)
        return True


def _refresh_links(conn: Any, page_id: int) -> None:
    page = conn.execute(
        "SELECT subject_id FROM kb_pages WHERE id=?", (page_id,)
    ).fetchone()
    if not page:
        return
    conn.execute("DELETE FROM kb_links WHERE source_page_id=?", (page_id,))
    text = "\n".join(
        row[0] or ""
        for row in conn.execute(
            "SELECT content FROM kb_blocks WHERE page_id=?", (page_id,)
        ).fetchall()
    )
    for label in sorted(set(x.strip() for x in _LINK_RE.findall(text) if x.strip())):
        target = conn.execute(
            """
            SELECT id FROM kb_pages
            WHERE subject_id=? AND title=? COLLATE NOCASE AND archived=0
            ORDER BY id LIMIT 1
            """,
            (page["subject_id"], label),
        ).fetchone()
        if not target:
            target = conn.execute(
                """
                SELECT id FROM kb_pages
                WHERE title=? COLLATE NOCASE AND archived=0
                ORDER BY id LIMIT 1
                """,
                (label,),
            ).fetchone()
        if target and int(target[0]) != page_id:
            conn.execute(
                "INSERT OR IGNORE INTO kb_links VALUES (?,?,?)",
                (page_id, int(target[0]), label),
            )


def get_backlinks(page_id: int) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in query(
            """
            SELECT p.id,p.title,s.name AS subject_name,l.link_text
            FROM kb_links l
            JOIN kb_pages p ON p.id=l.source_page_id
            JOIN kb_subjects s ON s.id=p.subject_id
            WHERE l.target_page_id=? AND p.archived=0
            ORDER BY p.updated_at DESC
            """,
            (page_id,),
        )
    ]


def get_outgoing_links(page_id: int) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in query(
            """
            SELECT p.id,p.title,s.name AS subject_name,l.link_text
            FROM kb_links l
            JOIN kb_pages p ON p.id=l.target_page_id
            JOIN kb_subjects s ON s.id=p.subject_id
            WHERE l.source_page_id=? AND p.archived=0
            ORDER BY p.title
            """,
            (page_id,),
        )
    ]


def list_versions(page_id: int, limit: int = 30) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in query(
            "SELECT id,title,reason,saved_at FROM kb_versions "
            "WHERE page_id=? ORDER BY id DESC LIMIT ?",
            (page_id, limit),
        )
    ]


def restore_version(page_id: int, version_id: int) -> None:
    rows = query(
        "SELECT title,blocks_json FROM kb_versions WHERE id=? AND page_id=?",
        (version_id, page_id),
    )
    if not rows:
        raise ValueError("版本不存在")
    save_page(
        page_id,
        rows[0]["title"],
        json.loads(rows[0]["blocks_json"]),
        reason=f"恢复版本 {version_id}",
    )


def set_tags(page_id: int, tags: Iterable[str]) -> None:
    names = sorted({tag.strip() for tag in tags if tag and tag.strip()})
    with transaction() as conn:
        conn.execute("DELETE FROM kb_page_tags WHERE page_id=?", (page_id,))
        for name in names:
            conn.execute("INSERT OR IGNORE INTO kb_tags(name) VALUES (?)", (name,))
            tag_id = conn.execute(
                "SELECT id FROM kb_tags WHERE name=? COLLATE NOCASE", (name,)
            ).fetchone()[0]
            conn.execute(
                "INSERT OR IGNORE INTO kb_page_tags(page_id,tag_id) VALUES (?,?)",
                (page_id, tag_id),
            )


def add_reflection(
    page_id: int, content: str, created_at: str | None = None, source_ref: str = ""
) -> None:
    if not content.strip():
        return
    with transaction() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO kb_reflections(page_id,content,created_at,source_ref)
            VALUES (?,?,?,?)
            """,
            (page_id, content.strip(), created_at or now(), source_ref or None),
        )


def search_pages(search_text: str, limit: int = 80) -> list[dict[str, Any]]:
    terms = [term for term in re.split(r"\s+", search_text.strip()) if term]
    if not terms:
        return []
    clauses: list[str] = []
    params: list[Any] = []
    for term in terms:
        clauses.append(
            "(p.title LIKE ? OR s.name LIKE ? OR EXISTS("
            "SELECT 1 FROM kb_blocks b WHERE b.page_id=p.id AND b.content LIKE ?"
            ") OR EXISTS(SELECT 1 FROM kb_tags t JOIN kb_page_tags pt "
            "ON pt.tag_id=t.id WHERE pt.page_id=p.id AND t.name LIKE ?))"
        )
        wildcard = f"%{term}%"
        params.extend([wildcard, wildcard, wildcard, wildcard])
    params.append(limit)
    return [
        dict(row)
        for row in query(
            f"""
            SELECT p.id,p.title,p.updated_at,p.kind,s.name AS subject_name,
                   (SELECT substr(replace(b.content, char(10), ' '),1,180)
                    FROM kb_blocks b
                    WHERE b.page_id=p.id AND b.content<>''
                    ORDER BY b.sort_order LIMIT 1) AS snippet
            FROM kb_pages p JOIN kb_subjects s ON s.id=p.subject_id
            WHERE p.archived=0 AND s.archived=0 AND {' AND '.join(clauses)}
            ORDER BY p.updated_at DESC LIMIT ?
            """,
            params,
        )
    ]


def move_page(
    page_id: int,
    *,
    new_parent_id: int | None,
    new_subject_id: int | None = None,
) -> None:
    page = get_page(page_id)
    if not page:
        raise ValueError("页面不存在")
    subject_id = new_subject_id or int(page["subject_id"])
    if new_parent_id == page_id:
        raise ValueError("页面不能成为自己的子页面")
    descendants = {p["id"] for p in descendants_of(page_id)}
    if new_parent_id in descendants:
        raise ValueError("不能把页面移动到自己的后代下面")
    with transaction() as conn:
        order = _next_page_order(conn, subject_id, new_parent_id)
        subtree_ids = [page_id, *sorted(descendants)]
        placeholders = ",".join("?" for _ in subtree_ids)
        conn.execute(
            f"UPDATE kb_pages SET subject_id=?,updated_at=? "
            f"WHERE id IN ({placeholders})",
            [subject_id, now(), *subtree_ids],
        )
        conn.execute(
            "UPDATE kb_pages SET parent_id=?,sort_order=?,updated_at=? "
            "WHERE id=?",
            (new_parent_id, order, now(), page_id),
        )


def descendants_of(page_id: int) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in query(
            """
            WITH RECURSIVE children(id,title,parent_id,subject_id) AS (
                SELECT id,title,parent_id,subject_id FROM kb_pages WHERE parent_id=?
                UNION ALL
                SELECT p.id,p.title,p.parent_id,p.subject_id
                FROM kb_pages p JOIN children c ON p.parent_id=c.id
            )
            SELECT * FROM children
            """,
            (page_id,),
        )
    ]


def archive_page(page_id: int, archived: bool = True) -> None:
    ids = [page_id] + [int(row["id"]) for row in descendants_of(page_id)]
    placeholders = ",".join("?" for _ in ids)
    with transaction() as conn:
        conn.execute(
            f"UPDATE kb_pages SET archived=?,updated_at=? WHERE id IN ({placeholders})",
            [1 if archived else 0, now(), *ids],
        )


def export_subject(subject_id: int) -> tuple[bytes, str]:
    subjects = [x for x in list_subjects(None) if int(x["id"]) == int(subject_id)]
    if not subjects:
        raise ValueError("学科不存在")
    subject = subjects[0]
    pages = list_pages(subject_id)
    by_parent: dict[int | None, list[dict[str, Any]]] = {}
    for page in pages:
        by_parent.setdefault(page["parent_id"], []).append(page)
    output = io.BytesIO()
    manifest: list[dict[str, Any]] = []
    used_names: set[str] = set()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for page in pages:
            full = get_page(int(page["id"]))
            if not full:
                continue
            safe = _safe_filename(full["title"])
            base = safe
            number = 2
            while safe.lower() in used_names:
                safe = f"{base}-{number}"
                number += 1
            used_names.add(safe.lower())
            lines = [f"# {full['title']}", ""]
            if full["tags"]:
                lines.extend([f"标签：{', '.join(full['tags'])}", ""])
            for block in full["blocks"]:
                kind = block["type"]
                content = block["content"]
                if kind == "heading1":
                    lines.extend([f"# {content}", ""])
                elif kind == "heading2":
                    lines.extend([f"## {content}", ""])
                elif kind == "heading3":
                    lines.extend([f"### {content}", ""])
                elif kind == "equation":
                    lines.extend(["$$", content, "$$", ""])
                elif kind == "callout":
                    lines.extend([f"> {content.replace(chr(10), chr(10) + '> ')}", ""])
                elif kind == "divider":
                    lines.extend(["---", ""])
                elif kind == "image" and block["asset_path"]:
                    asset_name = f"assets/{Path(block['asset_path']).name}"
                    lines.extend([f"![{content}]({asset_name})", ""])
                    raw_asset = _read_asset(block["asset_path"])
                    if raw_asset is not None:
                        zf.writestr(asset_name, raw_asset)
                else:
                    lines.extend([content, ""])
            filename = f"pages/{safe}.md"
            zf.writestr(filename, "\n".join(lines))
            manifest.append(
                {
                    "id": full["id"],
                    "title": full["title"],
                    "parent_id": full["parent_id"],
                    "file": filename,
                    "updated_at": full["updated_at"],
                }
            )
        zf.writestr(
            "manifest.json",
            json.dumps(
                {"subject": subject["name"], "pages": manifest},
                ensure_ascii=False,
                indent=2,
            ),
        )
    return output.getvalue(), f"{_safe_filename(subject['name'])}-知识库.zip"


def _safe_filename(value: str) -> str:
    clean = re.sub(r'[\\/:*?"<>|]+', "_", value).strip(" .")
    return clean or "未命名"


def copy_legacy_asset(path_value: str | Path, namespace: str = "legacy") -> str:
    source = Path(path_value)
    if not source.is_absolute():
        source = ROOT / source
    if not source.exists() or not source.is_file():
        return ""
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    extension = source.suffix.lower() or ".bin"
    relative = Path("attachments") / namespace / f"{digest}{extension}"
    if CLOUD_MODE:
        mime = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        _upload_asset(relative.as_posix(), source.read_bytes(), mime)
        return relative.as_posix()
    target = STATIC_DIR / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(source, target)
    return relative.as_posix()
