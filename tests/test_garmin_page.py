from datetime import datetime, timedelta

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from services import auth, garmin_accounts, garmin_link
from utils import bootstrap

GARMIN_PAGE = Path(__file__).resolve().parent.parent / "pages" / "garmin.py"


def _run_garmin(monkeypatch, *, hosted, has_credentials=False, session_state=None):
    monkeypatch.setattr(bootstrap, "ready", lambda: None)
    monkeypatch.setattr(
        auth, "runtime_config",
        lambda: ("test-db-url", "https://example.invalid", "test-key") if hosted else (None, None, None),
    )
    if hosted:
        monkeypatch.setattr(auth, "current_identity", lambda _key: ("garmin-test-user", "garmin@example.test"))
    creds = ("test@example.com", "testpassword") if has_credentials else None
    monkeypatch.setattr(garmin_accounts, "credentials", lambda _s, _u: creds)
    
    app = AppTest.from_file(GARMIN_PAGE)
    if session_state:
        for k, v in session_state.items():
            app.session_state[k] = v
    return app.run()


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

def test_sync_success_display(db, monkeypatch):
    """Verifies success display."""
    state = {
        "garmin_sync_last_success": datetime.now(),
        "garmin_sync_run_count": 5
    }
    app = _run_garmin(monkeypatch, hosted=True, has_credentials=True, session_state=state)
    assert len(app.success) == 1
    assert "Synced at" in app.success[0].value

def test_sync_stale_display(db, monkeypatch):
    """Verifies stale warning display."""
    state = {
        "garmin_sync_last_success": datetime.now() - timedelta(days=2),
        "garmin_sync_run_count": 5
    }
    app = _run_garmin(monkeypatch, hosted=True, has_credentials=True, session_state=state)
    assert len(app.warning) == 1
    assert "out of date" in app.warning[0].value

def test_sync_error_display(db, monkeypatch):
    """Verifies error display."""
    state = {
        "garmin_sync_error": "Garmin login failed"
    }
    app = _run_garmin(monkeypatch, hosted=True, has_credentials=True, session_state=state)
    assert len(app.error) == 1
    assert "Sync failed" in app.error[0].value

def test_failed_sync_no_success_state(db, monkeypatch):
    """Verifies a failed sync does not set success state."""
    # Simulate a button press that fails
    monkeypatch.setattr(garmin_accounts, "fetch_account_runs", lambda _s, _u: exec("raise Exception('Fail')"))
    
    app = _run_garmin(monkeypatch, hosted=True, has_credentials=True)
    
    # Click button
    app.button[0].click().run()
    
    assert len(app.error) == 1
    assert "Sync failed" in app.error[0].value
    assert "garmin_sync_last_success" not in app.session_state
