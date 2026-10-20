from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from services import auth


SETTINGS_PAGE = Path(__file__).resolve().parent.parent / "pages" / "settings.py"


def _run_settings(monkeypatch, *, hosted):
    from utils import bootstrap

    monkeypatch.setattr(bootstrap, "ready", lambda: None)
    monkeypatch.setattr(
        auth, "runtime_config",
        lambda: ("test-database-url", "https://example.invalid", "test-public-key")
        if hosted else (None, None, None),
    )
    if hosted:
        monkeypatch.setattr(auth, "current_identity", lambda _key: ("settings-ui-user", "ui@example.test"))
    return AppTest.from_file(SETTINGS_PAGE).run()


@pytest.mark.parametrize("hosted", [False, True], ids=["local", "hosted"])
def test_settings_groups_existing_controls_without_changing_available_paths(db, monkeypatch, hosted):
    app = _run_settings(monkeypatch, hosted=hosted)

    assert not app.exception
    assert [heading.value for heading in app.subheader] == ["Plan", "AI", "Data", "Garmin"]

    # Existing plan and AI controls remain labeled and reachable in the existing form.
    assert {widget.label for widget in app.text_input} >= {
        "Name", "Training goal", "Gemini API key (kept in this browser session only)"
    }
    assert {widget.label for widget in app.selectbox} >= {
        "Training focus", "Weekend run day", "Gemini model", "CSV table"
    }
    assert any(button.label == "Save and regenerate plan" for button in app.button)

    # Data controls remain available; Garmin continues to expose its existing mode-specific UI.
    assert any(widget.label == "JSON restore" for widget in app.get("file_uploader"))
    if hosted:
        assert {widget.label for widget in app.text_input} >= {"Your Garmin email", "Your Garmin password"}
        assert any(button.label == "Save Garmin connection" for button in app.button)
    else:
        assert any(button.label == "Sync last 30 days from Garmin" for button in app.button)
