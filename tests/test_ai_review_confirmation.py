import json
from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from database.session import session_scope
from models.orm import AIAdjustment, User
from services import ai_service as ai
from services import plan_service as ps
from services.athlete_profile import get_profile
from tests.conftest import START


AI_PAGE = Path(__file__).resolve().parent.parent / "pages" / "ai_coach.py"


def test_context_omits_age_weight_and_recovery_requires_fresh_consent(db):
    wellness = {
        "period": {"start": "2026-10-01", "end": "2026-10-21", "account": "private"},
        "metrics": {
            "hrv": [{"date": "2026-10-20", "lastNightAvg": 55, "access_token": "secret"}],
            "sleep": [{"date": "2026-10-20", "sleepScore": 80, "raw": {"private": True}}],
        },
        "tokens": {"access_token": "secret"},
    }
    with session_scope() as s:
        user = ps.ensure_user(s)
        profile = get_profile(s, user)
        profile.age = 42
        profile.body_weight_kg = 72.5
        profile.share_garmin_recovery_with_ai = True

        without_fresh_choice = ai.build_context(
            s, user, START, garmin_wellness=wellness
        )
        with_fresh_choice = ai.build_context(
            s, user, START, garmin_wellness=wellness, include_garmin_recovery=True
        )

    assert "age_years" not in without_fresh_choice["athlete_profile"]
    assert "body_weight_kg" not in without_fresh_choice["athlete_profile"]
    assert "share_garmin_recovery_with_ai" not in without_fresh_choice["athlete_profile"]
    assert "garmin_wellness" not in without_fresh_choice
    assert with_fresh_choice["garmin_wellness"] == {
        "metrics": {
            "hrv": [{"date": "2026-10-20", "lastNightAvg": 55}],
            "sleep": [{"date": "2026-10-20", "sleepScore": 80}],
        }
    }
    assert "secret" not in json.dumps(with_fresh_choice)
    assert '"account":' not in json.dumps(with_fresh_choice)
    assert '"private"' not in json.dumps(with_fresh_choice)


def test_request_payload_is_allowlisted_and_each_sensitive_category_is_separate(monkeypatch):
    captured = []

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}

    monkeypatch.setattr(ai.requests, "post", lambda _url, **kwargs: captured.append(kwargs) or Response())
    base = {
        "today": START.isoformat(),
        "athlete_profile": {
            "training_focus": "Running", "experience_level": "Beginner",
            "goal": "Consistency", "age_years": 35, "body_weight_kg": 80,
            "share_garmin_recovery_with_ai": True, "auth_uid": "private",
        },
        "recent_runs": [{"date": "2026-10-20", "km": 5, "activity_id": "private"}],
        "notes": [{"date": "2026-10-20", "category": "Training", "text": "Private note"}],
        "garmin_wellness": {
            "metrics": {"hrv": [{"date": "2026-10-20", "lastNightAvg": 50, "token": "private"}]},
            "raw_provider_object": "private",
        },
        "provider_token": "private",
    }

    def sent_payload(**consents):
        ai.request_proposals(base, "test-key", "gemini-test", **consents)
        return json.loads(captured[-1]["json"]["contents"][0]["parts"][0]["text"])

    declined = sent_payload()
    assert "notes" not in declined and "garmin_wellness" not in declined
    assert "provider_token" not in declined
    assert "activity_id" not in json.dumps(declined)
    assert "age_years" not in json.dumps(declined) and "body_weight_kg" not in json.dumps(declined)
    assert "share_garmin_recovery_with_ai" not in json.dumps(declined)

    notes_only = sent_payload(include_notes=True)
    assert notes_only["notes"][0]["text"] == "Private note"
    assert "garmin_wellness" not in notes_only

    recovery_only = sent_payload(include_garmin_recovery=True)
    assert "notes" not in recovery_only
    assert recovery_only["garmin_wellness"] == {
        "metrics": {"hrv": [{"date": "2026-10-20", "lastNightAvg": 50}]}
    }
    assert "private" not in json.dumps(recovery_only)


def test_preview_cancel_invalid_and_stale_preview_are_read_only(db):
    with session_scope() as s:
        user = ps.ensure_user(s)
        run = next(item for item in ps.all_runs(s, user)
                   if item.date >= date.today() and item.slot not in {"race", "long"})
        original = run.adjusted_km
        upcoming = ai.upcoming_sessions(s, user, date.today())
        valid_raw = [{
            "session_date": run.date.isoformat(), "kind": "reduce_volume",
            "new_km": round(max(0, original - 1), 1), "reason": "Recovery",
        }]
        proposals, errors, snapshot = ai.create_preview(valid_raw, upcoming, user.id)
        assert len(proposals) == 1 and not errors
        assert run.adjusted_km == original
        assert not ps.list_adjustments(s, user)

        no_proposals, no_errors, no_snapshot = ai.create_preview([], upcoming, user.id)
        assert not no_proposals and not no_errors and not no_snapshot
        assert run.adjusted_km == original
        assert not ps.list_adjustments(s, user)

        invalid, errors, invalid_snapshot = ai.create_preview(
            [{"session_date": "1900-01-01", "kind": "reduce_volume", "new_km": 1}],
            upcoming, user.id,
        )
        assert not invalid and errors and not invalid_snapshot
        assert run.adjusted_km == original
        assert not ps.list_adjustments(s, user)

        run.adjusted_km = round(original - 0.2, 1)
        with pytest.raises(ValueError, match="plan changed"):
            ai.apply_reviewed_proposals(
                s, user, [p.model_dump(mode="json") for p in proposals],
                [p.model_dump(mode="json") for p in proposals], snapshot, date.today(),
            )
        assert not ps.list_adjustments(s, user)


def test_confirmation_applies_only_selected_reviewed_proposal(db):
    with session_scope() as s:
        user = ps.ensure_user(s)
        runs = [item for item in ps.all_runs(s, user)
                if item.date >= date.today() and item.slot not in {"race", "long"}][:2]
        assert len(runs) == 2
        upcoming = ai.upcoming_sessions(s, user, date.today())
        raw = [
            {"session_date": item.date.isoformat(), "kind": "reduce_volume",
             "new_km": round(max(0, item.adjusted_km - 1), 1), "reason": "Reviewed"}
            for item in runs
        ]
        proposals, errors, snapshot = ai.create_preview(raw, upcoming, user.id)
        assert len(proposals) == 2 and not errors
        reviewed = [proposal.model_dump(mode="json") for proposal in proposals]
        chosen = [reviewed[0]]

        other = User(name="Other athlete", auth_uid="ai-preview-other-account")
        s.add(other)
        s.flush()
        with pytest.raises(ValueError, match="preview is no longer valid"):
            ai.apply_reviewed_proposals(s, other, reviewed, chosen, snapshot, date.today())
        assert not ps.list_adjustments(s, other)

        altered = [{**reviewed[0], "new_km": max(0, reviewed[0]["new_km"] - 0.1)}]
        with pytest.raises(ValueError, match="Selected changes"):
            ai.apply_reviewed_proposals(s, user, reviewed, altered, snapshot, date.today())
        assert not ps.list_adjustments(s, user)

        assert ai.apply_reviewed_proposals(
            s, user, reviewed, chosen, snapshot, date.today()
        ) == 1
        applied = s.query(AIAdjustment).filter_by(user_id=user.id, status="applied").all()
        assert len(applied) == 1
        assert applied[0].session_date == runs[0].date
        assert runs[0].adjusted_km == reviewed[0]["new_km"]
        assert runs[1].adjusted_km != reviewed[1]["new_km"]


def test_ai_page_preview_is_read_only_cancelable_and_confirmation_is_explicit(db, monkeypatch):
    from services import auth
    from utils import bootstrap

    monkeypatch.setattr(bootstrap, "ready", lambda: None)
    monkeypatch.setattr(bootstrap, "get_openai_key", lambda: "test-key")
    monkeypatch.setattr(auth, "runtime_config", lambda: (None, None, None))
    captured = []

    def request(context, _key, _model, **_consents):
        captured.append(context)
        session = context["upcoming"][0]
        return "Reviewed safely.", [{
            "session_date": session["date"], "kind": "reduce_volume",
            "new_km": max(0, session["adjusted_km"] - 1), "reason": "More recovery",
        }], []

    monkeypatch.setattr(ai, "request_proposals", request)
    app = AppTest.from_file(AI_PAGE).run()
    assert not app.exception
    disclosure = next(item.value for item in app.info if "Google Gemini" in item.value)
    assert "training plan" in disclosure
    assert "Age and body weight are not sent" in disclosure
    assert "recent notes" not in disclosure

    next(button for button in app.button if button.label == "Review my training").click().run()
    assert not app.exception
    assert "notes" not in captured[0]
    assert any("Review proposed plan changes" in item.value for item in app.subheader)
    with session_scope() as s:
        user = ps.ensure_user(s)
        assert not ps.list_adjustments(s, user)

    next(button for button in app.button if button.label == "Cancel preview").click().run()
    assert not app.exception
    with session_scope() as s:
        user = ps.ensure_user(s)
        assert not ps.list_adjustments(s, user)
        assert not s.query(AIAdjustment).filter_by(user_id=user.id, status="applied").all()

    next(button for button in app.button if button.label == "Review my training").click().run()
    next(button for button in app.button if button.label == "Confirm selected changes").click().run()
    assert not app.exception
    with session_scope() as s:
        user = ps.ensure_user(s)
        applied = s.query(AIAdjustment).filter_by(user_id=user.id, status="applied").all()
        assert len(applied) == 1


def test_legacy_pending_adjustments_are_read_only_and_cannot_be_approved(db, monkeypatch):
    from services import auth
    from utils import bootstrap

    monkeypatch.setattr(bootstrap, "ready", lambda: None)
    monkeypatch.setattr(bootstrap, "get_openai_key", lambda: "test-key")
    monkeypatch.setattr(auth, "runtime_config", lambda: (None, None, None))

    with session_scope() as s:
        user = ps.ensure_user(s)
        run = next(item for item in ps.all_runs(s, user) if item.date >= date.today())
        pending = AIAdjustment(
            user_id=user.id, session_date=run.date, kind="reduce_volume",
            old_km=run.adjusted_km, new_km=max(0, run.adjusted_km - 1),
            old_type=run.run_type, new_type=run.run_type,
            reason="Legacy proposal", status="pending",
        )
        s.add(pending)
        s.flush()
        pending_id = pending.id
        before_adjustment = (
            pending.status, pending.old_km, pending.new_km, pending.reason,
        )
        before_plan = [
            (item.id, item.adjusted_km, item.adjusted_type)
            for item in ps.all_runs(s, user)
        ]

    app = AppTest.from_file(AI_PAGE).run()
    assert not app.exception
    assert any(item.value == "AI change history" for item in app.subheader)
    assert any("Pending" in item.value for item in app.markdown)
    assert any("Legacy proposal" in item.value for item in app.caption)
    assert not any(button.label in {"Apply", "Reject"} for button in app.button)

    with session_scope() as s:
        user = ps.ensure_user(s)
        with pytest.raises(ValueError, match="Invalid adjustment status"):
            ai.set_status(s, user, pending_id, "approved", today=date.today())
        after_plan = [
            (item.id, item.adjusted_km, item.adjusted_type)
            for item in ps.all_runs(s, user)
        ]
        pending = s.get(AIAdjustment, pending_id)
        assert after_plan == before_plan
        assert (
            pending.status, pending.old_km, pending.new_km, pending.reason,
        ) == before_adjustment


def test_ai_page_garmin_recovery_choice_is_fresh_and_disclosed(db, monkeypatch):
    from services import auth
    from utils import bootstrap

    monkeypatch.setattr(bootstrap, "ready", lambda: None)
    monkeypatch.setattr(bootstrap, "get_openai_key", lambda: "test-key")
    monkeypatch.setattr(auth, "runtime_config", lambda: (None, None, None))
    requests = []

    def request(context, _key, _model, include_notes=False, include_garmin_recovery=False):
        requests.append((context, include_notes, include_garmin_recovery))
        return "No plan changes needed.", [], []

    monkeypatch.setattr(ai, "request_proposals", request)
    app = AppTest.from_file(AI_PAGE).run()
    app.session_state["garmin_wellness"] = {
        "metrics": {"hrv": [{"date": "2026-10-20", "lastNightAvg": 48}]}
    }
    app.run()
    recovery_choice = next(widget for widget in app.checkbox if "recovery metrics" in widget.label)
    assert recovery_choice.value is False
    disclosure = next(item.value for item in app.info if "Google Gemini" in item.value)
    assert "Garmin recovery metrics" not in disclosure

    recovery_choice.check().run()
    disclosure = next(item.value for item in app.info if "Google Gemini" in item.value)
    assert "compact Garmin recovery metrics" in disclosure
    next(button for button in app.button if button.label == "Review my training").click().run()
    assert not app.exception
    assert requests[-1][2] is True and requests[-1][1] is False
    assert "garmin_wellness" in requests[-1][0]
    assert app.session_state["ai_garmin_recovery_opt_in"] is False

    next(button for button in app.button if button.label == "Review my training").click().run()
    assert not app.exception
    assert requests[-1][2] is False
    assert "garmin_wellness" not in requests[-1][0]
    with session_scope() as s:
        user = ps.ensure_user(s)
        assert not ps.list_adjustments(s, user)
