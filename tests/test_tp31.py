
import json
import pytest
from services import ai_service as ai
from tests.conftest import START

def test_recovery_payload_consent_isolation(monkeypatch):
    captured = []

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}

    monkeypatch.setattr(ai.requests, "post", lambda _url, **kwargs: captured.append(kwargs) or Response())
    
    wellness = {"metrics": {"sleep": [{"date": "2026-10-20", "sleepScore": 80}]}}
    base = {
        "today": START.isoformat(),
        "garmin_wellness": wellness,
    }

    # Test opt-out
    ai.request_proposals(base, "test-key", "gemini-test", include_garmin_recovery=False)
    declined_payload = json.loads(captured[-1]["json"]["contents"][0]["parts"][0]["text"])
    assert "garmin_wellness" not in declined_payload

    # Test opt-in
    ai.request_proposals(base, "test-key", "gemini-test", include_garmin_recovery=True)
    accepted_payload = json.loads(captured[-1]["json"]["contents"][0]["parts"][0]["text"])
    assert "garmin_wellness" in accepted_payload
    assert accepted_payload["garmin_wellness"]["metrics"]["sleep"][0]["sleepScore"] == 80
