"""SMTP email delivery for channel='email' hooks."""
from unittest.mock import MagicMock, patch

from api.services.notifier import _resolve_smtp_config, _send_email, parse_mailto, render_template


def test_parse_mailto_extracts_recipients():
    assert parse_mailto("mailto:a@x.com,b@y.com") == ["a@x.com", "b@y.com"]
    assert parse_mailto("mailto:a@x.com") == ["a@x.com"]
    assert parse_mailto("https://hooks.slack.com/x") == []


# ---------------------------------------------------------------------------
# render_template — lightweight {{var}} substitution for email hook bodies.
# ---------------------------------------------------------------------------

def test_render_template_substitutes_known_vars():
    out = render_template("Run {{run_id}} finished: {{status}}", {"run_id": "abc-123", "status": "FAILED"})
    assert out == "Run abc-123 finished: FAILED"


def test_render_template_blanks_unknown_vars():
    out = render_template("Hello {{missing}}!", {"run_id": "abc-123"})
    assert out == "Hello !"


def test_render_template_blanks_none_values():
    out = render_template("Owner: {{owner}}", {"owner": None})
    assert out == "Owner: "


def test_render_template_stringifies_non_string_values():
    out = render_template("Failed: {{failed}}", {"failed": 3})
    assert out == "Failed: 3"


def test_render_template_leaves_plain_text_untouched():
    assert render_template("no placeholders here", {}) == "no placeholders here"


# ---------------------------------------------------------------------------
# _send_email — pure function, takes an explicit config so these never touch
# the DB or real env vars.
# ---------------------------------------------------------------------------

def test_send_email_fails_without_host():
    result = _send_email(["a@x.com"], "subj", "body", config={"host": ""})
    assert result.ok is False
    assert "not configured" in result.error


@patch("smtplib.SMTP")
def test_send_email_uses_given_config(mock_smtp):
    server = MagicMock()
    mock_smtp.return_value.__enter__.return_value = server

    result = _send_email(["a@x.com"], "ETL run FAILED", "run abc-123 failed",
                          config={"host": "mail.internal", "port": 587, "from_addr": "etl@corp.local"})

    assert result.ok is True
    mock_smtp.assert_called_once_with("mail.internal", 587, timeout=10)
    server.send_message.assert_called_once()


@patch("smtplib.SMTP")
def test_send_email_starttls_and_login(mock_smtp):
    server = MagicMock()
    mock_smtp.return_value.__enter__.return_value = server

    result = _send_email(["a@x.com"], "subj", "body", config={
        "host": "mail.internal", "port": 587, "use_tls": True, "user": "svc", "password": "secret",
    })

    assert result.ok is True
    server.starttls.assert_called_once()
    server.login.assert_called_once_with("svc", "secret")


@patch("smtplib.SMTP")
def test_send_email_does_not_raise_on_smtp_error(mock_smtp):
    mock_smtp.return_value.__enter__.side_effect = Exception("connection refused")

    result = _send_email(["a@x.com"], "subj", "body", config={"host": "mail.internal", "port": 25})

    assert result.ok is False
    assert "connection refused" in result.error


def test_send_email_resolves_config_when_none_given(monkeypatch):
    monkeypatch.delenv("ETL_SMTP_HOST", raising=False)
    # No config passed -> falls through to _resolve_smtp_config(), which with
    # no DB host and no env var configured reports "not configured".
    with patch("api.services.notifier._resolve_smtp_config", return_value={"host": ""}):
        result = _send_email(["a@x.com"], "subj", "body")
    assert result.ok is False


# ---------------------------------------------------------------------------
# _resolve_smtp_config — DB-configured settings win; env vars are the fallback.
# ---------------------------------------------------------------------------

@patch("etl_framework.repository.repository.SettingsRepository")
@patch("etl_framework.repository.database.SessionLocal")
def test_resolve_smtp_config_prefers_db(mock_session_local, mock_repo_cls, monkeypatch):
    monkeypatch.setenv("ETL_SMTP_HOST", "env-host")
    mock_repo_cls.return_value.get_smtp_config.return_value = {
        "host": "db-host", "port": 2525, "from_addr": "db@corp.local",
        "user": "dbuser", "password": "dbpass", "use_tls": True,
    }

    cfg = _resolve_smtp_config()

    assert cfg["host"] == "db-host"
    assert cfg["port"] == 2525


@patch("etl_framework.repository.repository.SettingsRepository")
@patch("etl_framework.repository.database.SessionLocal")
def test_resolve_smtp_config_falls_back_to_env_when_db_blank(mock_session_local, mock_repo_cls, monkeypatch):
    monkeypatch.setenv("ETL_SMTP_HOST", "env-host")
    monkeypatch.setenv("ETL_SMTP_PORT", "2525")
    mock_repo_cls.return_value.get_smtp_config.return_value = {
        "host": "", "port": 587, "from_addr": "", "user": "", "password": "", "use_tls": False,
    }

    cfg = _resolve_smtp_config()

    assert cfg["host"] == "env-host"
    assert cfg["port"] == 2525


@patch("etl_framework.repository.database.SessionLocal", side_effect=Exception("db down"))
def test_resolve_smtp_config_falls_back_to_env_on_db_error(mock_session_local, monkeypatch):
    monkeypatch.setenv("ETL_SMTP_HOST", "env-host")

    cfg = _resolve_smtp_config()

    assert cfg["host"] == "env-host"
