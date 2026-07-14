"""Reset the admin access token to a specific raw value."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from etl_framework.repository.database import DATABASE_URL
from etl_framework.repository.models import ApiToken

RAW_TOKEN = "etl_7f27a4b70ed746a0ca1029f9afa26a98d25d41e553e74641b682f4e5ffb6f532"
NAME = "admin"


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def main() -> None:
    token_hash = _hash(RAW_TOKEN)
    token_hint = RAW_TOKEN[-8:]

    engine = create_engine(DATABASE_URL)
    with Session(engine) as db:
        db.query(ApiToken).filter(ApiToken.is_admin == True).update(
            {"enabled": False}, synchronize_session=False
        )
        existing = db.query(ApiToken).filter(ApiToken.token_hash == token_hash).first()
        if existing:
            existing.enabled = True
            existing.is_admin = True
            existing.name = NAME
            existing.token_hint = token_hint
            existing.expires_at = None
            print(f"Updated existing token id={existing.id}")
        else:
            token = ApiToken(
                token_hash=token_hash,
                name=NAME,
                created_at=datetime.now(timezone.utc),
                last_used_at=None,
                expires_at=None,
                enabled=True,
                is_admin=True,
                token_hint=token_hint,
            )
            db.add(token)
            print("Created new admin token")
        db.commit()
    print("Admin token reset complete")
    print(f"Token: {RAW_TOKEN}")


if __name__ == "__main__":
    main()
