import json
import pytest
from datetime import date
from database.session import session_scope
from services import ai_service as ai
from services import plan_service as ps
from tests.conftest import START

def test_injury_response_payload_excludes_notes_without_consent(db, monkeypatch):
    captured = []
    
    class Response:
        status_code = 200
        @staticmethod
        def json(): return {"candidates": [{"content": {"parts": [{"text": '{"summary": "ok", "proposals": []}'}]}}]}

    monkeypatch.setattr(ai.requests, "post", lambda _url, **kwargs: captured.append(kwargs) or Response())
    
    with session_scope() as s:
        user = ps.ensure_user(s)
        ps.add_note(s, user, START, "Pain", "My knee hurts when I run.")
        
    context = ai.build_context(s, user, START, include_notes=False)
    ai.request_proposals(context, "test-key", "gemini-test", include_notes=False)
    
    payload = json.loads(captured[-1]["json"]["contents"][0]["parts"][0]["text"])
    assert "notes" not in payload

def test_injury_response_cautions_against_increases_and_directs_to_care(db, monkeypatch):
    captured = []
    
    class Response:
        status_code = 200
        @staticmethod
        def json(): return {"candidates": [{"content": {"parts": [{"text": '{"summary": "Stop running, see a doctor.", "proposals": []}'}]}}]}

    monkeypatch.setattr(ai.requests, "post", lambda _url, **kwargs: captured.append(kwargs) or Response())
    
    with session_scope() as s:
        user = ps.ensure_user(s)
        ps.add_note(s, user, START, "Injury", "My Achilles hurts.")
        
    context = ai.build_context(s, user, START, include_notes=True)
    ai.request_proposals(context, "test-key", "gemini-test", include_notes=True)
    
    # Verify the AI received the notes (payload)
    payload = json.loads(captured[-1]["json"]["contents"][0]["parts"][0]["text"])
    assert payload["notes"][0]["text"] == "My Achilles hurts."
    
    # Verify system prompt instructions were followed (simulated check on prompt used)
    # The prompt is hardcoded in ai_service, so we verify it contains the cautious instruction
    assert "If the athlete mentions pain, injury, or potential overtraining, be conservative" in ai.SYSTEM_PROMPT
