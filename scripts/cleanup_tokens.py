"""Nuclear cleanup: disable all tokens, delete stale ones, and reset admin token.

Usage:
  python scripts/cleanup_tokens.py                        # cleanup + reset admin to random
  python scripts/cleanup_tokens.py --raw-token etl_...    # cleanup + reset admin to specific value
  python scripts/cleanup_tokens.py --list-only
"""
from __future__ import annotations

import argparse
import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import create_engine, delete
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
    print(f"\n{'ID':<5} {'Name':<25} {'Admin':<6} {'Enabled':<8} {'Hint':<12}")
    print("-" * 60)
    for t in tokens:
        print(
            f"{t.id:<5} {t.name:<25} {t.is_admin:<6} {t.enabled:<8} "
            f"{t.token_hint or '(none)':<12}"
        )


def disable_non_admin(db: Session) -> int:
    count = (
        db.query(ApiToken)
        .filter(ApiToken.is_admin == False, ApiToken.enabled == True)  # noqa: E712
        .update({"enabled": False}, synchronize_session=False)
    )
    db.commit()
    return count


def delete_disabled(db: Session) -> int:
    stmt = delete(ApiToken).where(ApiToken.enabled == False)  # noqa: E712
    result = db.execute(stmt)
    db.commit()
    return result.rowcount


def disable_other_admins(db: Session, keep_id: int) -> int:
    count = (
        db.query(ApiToken)
        .filter(ApiToken.is_admin == True, ApiToken.id != keep_id)  # noqa: E712
        .update({"enabled": False}, synchronize_session=False)
    )
    db.commit()
    return count


def reset_admin(db: Session, raw_token: str | None = None, name: str = "admin") -> tuple[str, ApiToken]:
    if raw_token is None:
        raw_token = "etl_" + secrets.token_hex(32)

    token_hash = _hash(raw_token)
    token_hint = raw_token[-8:]

    existing = db.query(ApiToken).filter(ApiToken.is_admin == True).first()  # noqa: E712
    if existing:
        existing.token_hash = token_hash
        existing.name = name
        existing.token_hint = token_hint
        existing.enabled = True
        existing.is_admin = True
        existing.expires_at = None
        token_obj = existing
    else:
        token_obj = ApiToken(
            token_hash=token_hash,
            name=name,
            created_at=datetime.now(timezone.utc),
            last_used_at=None,
            expires_at=None,
            enabled=True,
            is_admin=True,
            token_hint=token_hint,
        )
        db.add(token_obj)
    db.commit()
    db.refresh(token_obj)
    return raw_token, token_obj


def main() -> None:
    parser = argparse.ArgumentParser(description="Nuclear token cleanup + admin reset")
    parser.add_argument("--list-only", action="store_true", help="Only list tokens, no changes")
    parser.add_argument("--raw-token", type=str, default=None, help="Specific raw token to set")
    parser.add_argument("--skip-reset", action="store_true", help="Skip admin token reset")
    args = parser.parse_args()

    engine = create_engine(DATABASE_URL)
    with Session(engine) as db:
        if args.list_only:
            list_tokens(db)
            return

        print("=== Before ===")
        list_tokens(db)

        print("\n=== Step 1: disable non-admin tokens ===")
        disabled = disable_non_admin(db)
        print(f"Disabled {disabled} non-admin token(s).")

        print("\n=== Step 2: disable duplicate admin tokens ===")
        admin_count = db.query(ApiToken).filter(ApiToken.is_admin == True).count()  # noqa: E712
        if admin_count > 1:
            skipped = disable_other_admins(db, keep_id=1)
            print(f"Disabled {skipped} duplicate admin token(s).")
        else:
            print("No duplicate admin tokens found.")

        print("\n=== Step 3: delete disabled tokens ===")
        deleted = delete_disabled(db)
        print(f"Deleted {deleted} disabled token(s).")

        if not args.skip_reset:
            print("\n=== Step 4: reset admin token ===")
            raw, token_obj = reset_admin(db, raw_token=args.raw_token)
            print(f"Admin token reset complete.")
            print(f"  ID:               {token_obj.id}")
            print(f"  Name:             {token_obj.name}")
            print(f"  Token hint:       ...{token_obj.token_hint}")
            print(f"  Raw token value:  {raw}")

        print("\n=== After ===")
        list_tokens(db)

    print("\nBrowser-side: close the tab completely and reopen to clear sessionStorage.")


if __name__ == "__main__":
    main()
