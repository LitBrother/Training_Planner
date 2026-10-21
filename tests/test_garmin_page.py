from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from services import auth, garmin_accounts
from utils import bootstrap

GARMIN_PAGE = Path(__file__).resolve().parent.parent / "pages" / "garmin.py"


def _run_garmin(monkeypatch, *, hosted, has_credentials=False):
    monkeypatch.setattr(bootstrap, "ready", lambda: None)
    monkeypatch.setattr(
        auth, "runtime_config",
        lambda: ("test-db-url", "https://example.invalid", "test-key") if hosted else (None, None, None),
    )
    if hosted:
        monkeypatch.setattr(auth, "current_identity", lambda _key: ("garmin-test-user", "garmin@example.test"))
    creds = ("test@example.com", "testpassword") if has_credentials else None
    monkeypatch.setattr(garmin_accounts, "credentials", lambda _s, _u: creds)
    return AppTest.from_file(GARMIN_PAGE).run()


def test_hosted_no_credentials_shows_settings_path(db, monkeypatch):
    """Hosted user with no saved credentials sees a pointer to Settings → Garmin."""
    app = _run_garmin(monkeypatch, hosted=True, has_credentials=False)

    assert not app.exception

    # The info message must mention "Settings"
    info_texts = [el.value for el in app.info]
    assert any("Settings" in t for t in info_texts), (
        f"Expected an info element containing 'Settings', got: {info_texts}"
    )

    # The stale blocker copy must be gone
    all_text = " ".join(
        el.value for collection in (app.info, app.success, app.warning, app.error)
        for el in collection
    )
    assert "not yet isolated" not in all_text


def test_hosted_with_credentials_shows_sync_button(db, monkeypatch):
    """Hosted user with saved credentials sees the per-account sync button."""
    app = _run_garmin(monkeypatch, hosted=True, has_credentials=True)

    assert not app.exception

    button_labels = [b.label for b in app.button]
    assert "Sync my Garmin runs" in button_labels, (
        f"Expected 'Sync my Garmin runs' button, got: {button_labels}"
    )

    # The stale blocker copy must be gone
    all_text = " ".join(
        el.value for collection in (app.info, app.success, app.warning, app.error)
        for el in collection
    )
    assert "not yet isolated" not in all_text


def test_local_mode_unchanged(db, monkeypatch):
    """Local (non-hosted) mode still shows the original sync-and-auto-match button."""
    app = _run_garmin(monkeypatch, hosted=False)

    assert not app.exception

    button_labels = [b.label for b in app.button]
    assert "Sync and auto-match" in button_labels, (
        f"Expected 'Sync and auto-match' button, got: {button_labels}"
    )
