import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from etl_framework.repository.database import Base
from etl_framework.repository.repository import TokenRepository, _TOKEN_MAX_TTL_DAYS

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()

def test_create_stores_token_hint(db):
    raw, token = TokenRepository(db).create("ci-pipeline")
    assert token.token_hint == raw[-8:]
    assert len(token.token_hint) == 8

def test_revoke_returns_token_hash(db):
    raw, token = TokenRepository(db).create("to-revoke")
    token_hash = token.token_hash
    result = TokenRepository(db).revoke(token.id)
    assert result == token_hash

def test_revoke_missing_returns_none(db):
    result = TokenRepository(db).revoke(9999)
    assert result is None

def test_expires_at_none_preserved(db):
    _, token = TokenRepository(db).create("perpetual", expires_at=None)
    assert token.expires_at is None

def test_expires_at_within_cap_preserved(db):
    short = datetime.now(timezone.utc) + timedelta(hours=1)
    _, token = TokenRepository(db).create("ci-short", expires_at=short)
    assert token.expires_at is not None
    delta = abs((token.expires_at.replace(tzinfo=timezone.utc) - short).total_seconds())
    assert delta < 2

def test_expires_at_beyond_cap_clamped(db):
    far_future = datetime.now(timezone.utc) + timedelta(days=_TOKEN_MAX_TTL_DAYS + 365)
    _, token = TokenRepository(db).create("ci-long", expires_at=far_future)
    cap = datetime.now(timezone.utc) + timedelta(days=_TOKEN_MAX_TTL_DAYS)
    stored = token.expires_at
    if stored.tzinfo is None:
        stored = stored.replace(tzinfo=timezone.utc)
    assert stored <= cap + timedelta(seconds=5)
    assert stored < far_future

def test_expires_at_naive_datetime_handled(db):
    naive = datetime.utcnow() + timedelta(days=10)
    assert naive.tzinfo is None
    _, token = TokenRepository(db).create("ci-naive", expires_at=naive)
    assert token.expires_at is not None
