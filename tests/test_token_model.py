from etl_framework.repository.models import ApiToken


def test_api_token_has_admin_and_hint_columns():
    cols = {c.key for c in ApiToken.__table__.columns}
    assert "is_admin" in cols
    assert "token_hint" in cols
