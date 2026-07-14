"""Clean up API tokens and optionally reset the admin token to a known value."""
from __future__ import annotations

import argparse
import hashlib
import secrets
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from etl_framework.repository.database import DATABASE_URL
from etl_framework.repository.models import ApiToken


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def list_tokens(db: Session) -> None:
    tokens = db.query(ApiToken).order_by(ApiToken.id).all()
    if not tokens:
        print("No tokens found.")
        return
    print(f"\n{'ID':<5} {'Name':<20} {'Admin':<6} {'Enabled':<8} {'Hint':<12} {'Created':<20}")
    print("-" * 80)
    for t in tokens:
        print(
            f"{t.id:<5} {t.name:<20} {t.is_admin:<6} {t.enabled:<8} "
            f"{t.token_hint:<12} {str(t.created_at):<20}"
        )


def cleanup_tokens(db: Session, dry_run: bool = False) -> int:
    """Disable all non-admin tokens. Keep exactly one enabled admin token."""
    tokens = db.query(ApiToken).all()
    admin_tokens = [t for t in tokens if t.is_admin and t.enabled]
    non_admin_enabled = [t for t in tokens if not t.is_admin and t.enabled]

    print(f"\nFound {len(admin_tokens)} enabled admin token(s).")
    print(f"Found {len(non_admin_enabled)} enabled non-admin token(s) to disable.")

    if dry_run:
        print("(dry run — no changes made)")
        return len(non_admin_enabled)

    for t in non_admin_enabled:
        t.enabled = False
        print(f"  Disabled token id={t.id} name={t.name!r}")
    db.commit()
    return len(non_admin_enabled)


def reset_admin_token(
    db: Session,
    raw_token: str | None = None,
    name: str = "admin",
) -> str:
    """Reset the admin token to `raw_token` (or generate a new one)."""
    if raw_token is None:
        raw_token = "etl_" + secrets.token_hex(32)

    token_hash = _hash(raw_token)
    token_hint = raw_token[-8:]

    existing_admin = db.query(ApiToken).filter(ApiToken.is_admin == True).first()
    if existing_admin:
        existing_admin.token_hash = token_hash
        existing_admin.name = name
        existing_admin.token_hint = token_hint
        existing_admin.enabled = True
        existing_admin.is_admin = True
        existing_admin.expires_at = None
        print(f"  Updated admin token id={existing_admin.id}")
    else:
        token = ApiToken(
            token_hash=token_hash,
            name=name,
            created_at=datetime.now(timezone.utc),
            last_used_at=None,
            expires_at=None,
            enabled=True,
            is_admin=True,
            token_hint=token_hint,
        )
        db.add(token)
        print("  Created new admin token")
    db.commit()
    return raw_token


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage API access tokens")
    parser.add_argument("--list", action="store_true", help="List all tokens")
    parser.add_argument("--cleanup", action="store_true", help="Disable all non-admin tokens")
    parser.add_argument("--dry-run", action="store_true", help="Show what cleanup would do")
    parser.add_argument("--reset", action="store_true", help="Reset admin token to a new value")
    parser.add_argument(
        "--raw-token",
        type=str,
        default=None,
        help="Specific raw token value to set (default: generate random)",
    )
    args = parser.parse_args()

    engine = create_engine(DATABASE_URL)
    with Session(engine) as db:
        if args.list or (not args.cleanup and not args.reset):
            list_tokens(db)

        if args.cleanup:
            cleanup_tokens(db, dry_run=args.dry_run)

        if args.reset:
            raw = reset_admin_token(db, raw_token=args.raw_token)
            print(f"\nAdmin token value (store this securely):\n  {raw}")
            print(f"  Token hint: ...{raw[-8:]}")
            list_tokens(db)


if __name__ == "__main__":
    main()
