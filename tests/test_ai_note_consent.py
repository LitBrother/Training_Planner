import json
from datetime import date
from pathlib import Path

from streamlit.testing.v1 import AppTest

from database.session import session_scope
from services import ai_service as ai
from services import plan_service as ps
from tests.conftest import START


AI_PAGE = Path(__file__).resolve().parent.parent / "pages" / "ai_coach.py"


def test_note_context_requires_opt_in_and_is_account_scoped(db):
    with session_scope() as s:
        owner = ps.ensure_user(s, auth_uid="ai-note-owner")
        other = ps.ensure_user(s, auth_uid="ai-note-other")
        ps.add_note(s, owner, START, "Training", "Owner private note")
        ps.add_note(s, other, START, "Injury", "Other private note")

        declined = ai.build_context(s, owner, START)
        accepted = ai.build_context(s, owner, START, include_notes=True)

        assert "notes" not in declined
        assert [row["text"] for row in accepted["notes"]] == ["Owner private note"]
        assert "Other private note" not in json.dumps(accepted)


def test_gemini_payload_requires_request_opt_in_and_errors_are_redacted(monkeypatch):
    captured = []

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}

    monkeypatch.setattr(ai.requests, "post", lambda _url, **kwargs: captured.append(kwargs) or Response())
    base = {"today": START.isoformat(), "notes": [{"text": "Sensitive note text"}]}

    ai.request_proposals(base, "test-key", "gemini-test")
    declined_payload = json.loads(captured[-1]["json"]["contents"][0]["parts"][0]["text"])
    assert "notes" not in declined_payload

    ai.request_proposals(base, "test-key", "gemini-test", include_notes=True)
    accepted_payload = json.loads(captured[-1]["json"]["contents"][0]["parts"][0]["text"])
    assert accepted_payload["notes"][0]["text"] == "Sensitive note text"

    class ErrorResponse:
        status_code = 400
        text = "Sensitive note text echoed by provider"

    monkeypatch.setattr(ai.requests, "post", lambda *_args, **_kwargs: ErrorResponse())
    try:
        ai.request_proposals(base, "test-key", "gemini-test", include_notes=True)
    except RuntimeError as exc:
        assert "Sensitive note text" not in str(exc)
    else:
        raise AssertionError("Expected the failed provider response to raise")


def test_malformed_successful_provider_response_is_handled_and_redacted(db, monkeypatch):
    from services import auth
    from utils import bootstrap

    monkeypatch.setattr(bootstrap, "ready", lambda: None)
    monkeypatch.setattr(bootstrap, "get_openai_key", lambda: "test-key")
    monkeypatch.setattr(auth, "runtime_config", lambda: (None, None, None))
    provider_requests = []

    class MalformedSuccessResponse:
        status_code = 200

        @staticmethod
        def json():
            # Valid provider envelope, but the generated JSON has the wrong top-level shape.
            return {
                "candidates": [{
                    "content": {
                        "parts": [{"text": '[{"message": "Sensitive note text"}]'}]
                    }
                }]
            }

    def post(_url, **kwargs):
        provider_requests.append(kwargs)
        return MalformedSuccessResponse()

    monkeypatch.setattr(ai.requests, "post", post)
    with session_scope() as s:
        user = ps.ensure_user(s)
        ps.add_note(s, user, START, "Training", "Sensitive note text")

    app = AppTest.from_file(AI_PAGE).run()
    assert not app.exception
    checkbox = next(widget for widget in app.checkbox if "recent note text" in widget.label)
    checkbox.check()
    next(button for button in app.button if button.label == "Review my training").click().run()

    assert not app.exception
    assert len(provider_requests) == 1
    prompt = provider_requests[0]["json"]["contents"][0]["parts"][0]["text"]
    assert "Sensitive note text" in prompt
    assert any("AI request failed" in item.value for item in app.error)
    assert all("Sensitive note text" not in item.value for item in app.error)
    assert all('[{"message": "Sensitive note text"}]' not in item.value for item in app.error)


def test_ai_ui_requires_fresh_affirmative_consent_and_clears_after_submission(db, monkeypatch):
    from services import auth
    from utils import bootstrap

    monkeypatch.setattr(bootstrap, "ready", lambda: None)
    monkeypatch.setattr(bootstrap, "get_openai_key", lambda: "test-key")
    monkeypatch.setattr(auth, "runtime_config", lambda: (None, None, None))
    captured = []
    fail = {"enabled": False}

    def request(context, _key, _model, include_notes=False, include_garmin_recovery=False):
        captured.append((context, include_notes, include_garmin_recovery))
        if fail["enabled"]:
            raise ai.AIRequestError("Sensitive note text must not appear in the UI")
        return "Reviewed.", [], []

    monkeypatch.setattr(ai, "request_proposals", request)
    with session_scope() as s:
        user = ps.ensure_user(s)
        ps.add_note(s, user, START, "Training", "Private UI note")

    app = AppTest.from_file(AI_PAGE).run()
    assert not app.exception
    checkbox = next(widget for widget in app.checkbox if "recent note text" in widget.label)
    assert checkbox.value is False
    assert "unchecked" not in checkbox.label.lower()

    next(button for button in app.button if button.label == "Review my training").click().run()
    assert not app.exception
    assert captured[-1][1] is False and captured[-1][2] is False
    assert "notes" not in captured[-1][0]
    assert app.session_state["ai_notes_opt_in"] is False

    checkbox = next(widget for widget in app.checkbox if "recent note text" in widget.label)
    checkbox.check().run()
    disclosure = next(item.value for item in app.info if "Google Gemini" in item.value)
    assert "recent notes (date, category, and text)" in disclosure
    next(button for button in app.button if button.label == "Review my training").click().run()
    assert not app.exception
    assert captured[-1][1] is True and captured[-1][2] is False
    assert captured[-1][0]["notes"][0]["text"] == "Private UI note"
    assert app.session_state["ai_notes_opt_in"] is False

    next(button for button in app.button if button.label == "Review my training").click().run()
    assert not app.exception
    assert captured[-1][1] is False and captured[-1][2] is False
    assert "notes" not in captured[-1][0]
    assert app.session_state["ai_notes_opt_in"] is False

    fail["enabled"] = True
    checkbox = next(widget for widget in app.checkbox if "recent note text" in widget.label)
    checkbox.check()
    next(button for button in app.button if button.label == "Review my training").click().run()
    assert not app.exception
    assert app.session_state["ai_notes_opt_in"] is False
    assert any("AI request failed" in item.value for item in app.error)
    assert all("Sensitive note text" not in item.value for item in app.error)


def test_ai_ui_clears_note_consent_when_account_changes(db, monkeypatch):
    from services import auth
    from utils import bootstrap

    monkeypatch.setattr(bootstrap, "ready", lambda: None)
    monkeypatch.setattr(bootstrap, "get_openai_key", lambda: "test-key")
    monkeypatch.setattr(
        auth, "runtime_config",
        lambda: ("test-database-url", "https://example.invalid", "test-public-key"),
    )
    identity = {"uid": "consent-account-one"}
    monkeypatch.setattr(auth, "current_identity", lambda _key: (identity["uid"], "athlete@example.test"))
    monkeypatch.setattr(ai, "request_proposals", lambda *_args, **_kwargs: ("", [], []))

    app = AppTest.from_file(AI_PAGE).run()
    app.session_state["ai_notes_opt_in"] = True

    identity["uid"] = "consent-account-two"
    app.run()
    assert not app.exception
    assert app.session_state["ai_notes_opt_in"] is False
    assert app.session_state["_ai_notes_consent_identity"] == "consent-account-two"
    assert app.session_state["ai_notes_opt_in"] is False
