from __future__ import annotations

import argparse
import json
import urllib.parse
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--service-key", default="")
    parser.add_argument("--public-url", default="")
    parser.add_argument("--app-password", default="")
    parser.add_argument(
        "--activate-local",
        action="store_true",
        help="Also make the local Streamlit app use the cloud database",
    )
    args = parser.parse_args()

    target = ROOT / ".streamlit" / "cloud-secrets.toml"
    existing: dict[str, str] = {}
    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                continue
            key, raw_value = line.split("=", 1)
            try:
                existing[key.strip()] = str(json.loads(raw_value.strip()))
            except json.JSONDecodeError:
                continue

    bootstrap_path = ROOT / "backups" / ".cloud-bootstrap.json"
    bootstrap = json.loads(bootstrap_path.read_text(encoding="utf-8"))
    password = str(bootstrap["dbPassword"])
    encoded_password = urllib.parse.quote(password, safe="")
    database_url = (
        f"postgresql://postgres.{args.project_ref}:{encoded_password}"
        f"@aws-0-{args.region}.pooler.supabase.com:6543/postgres"
    )
    values = {
        "SUPABASE_DB_URL": database_url,
        "SUPABASE_URL": f"https://{args.project_ref}.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": args.service_key
        or existing.get("SUPABASE_SERVICE_ROLE_KEY", ""),
        "SUPABASE_STORAGE_BUCKET": "mynotebook-assets",
        "MYNOTEBOOK_PUBLIC_URL": args.public_url
        or existing.get("MYNOTEBOOK_PUBLIC_URL", ""),
        "APP_PASSWORD": args.app_password or existing.get("APP_PASSWORD", ""),
    }
    lines = [f"{key} = {json.dumps(value)}" for key, value in values.items()]
    content = "\n".join(lines) + "\n"
    target.write_text(content, encoding="utf-8")
    if args.activate_local:
        (ROOT / ".streamlit" / "secrets.toml").write_text(
            content, encoding="utf-8"
        )
    print("cloud_secrets_written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
