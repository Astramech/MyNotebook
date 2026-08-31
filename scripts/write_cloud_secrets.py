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
    args = parser.parse_args()

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
        "SUPABASE_SERVICE_ROLE_KEY": args.service_key,
        "SUPABASE_STORAGE_BUCKET": "mynotebook-assets",
        "MYNOTEBOOK_PUBLIC_URL": args.public_url,
    }
    lines = [f"{key} = {json.dumps(value)}" for key, value in values.items()]
    target = ROOT / ".streamlit" / "cloud-secrets.toml"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("cloud_secrets_written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
