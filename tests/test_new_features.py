from datetime import date, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from database.session import session_scope
from models.orm import AIAdjustment, StrengthLog
from models.schemas import PlanSettings
from services import ai_service as ai
from services import export_service as ex
from services import garmin_accounts
from services import plan_service as ps
from tests.conftest import START
from utils.dates import monday_of


def test_ai_proposal_caps_and_taper_rules():
    first, second = date(2026, 10, 13), date(2026, 10, 16)
    upcoming = {
        first: {"slot": "quality", "type": "Threshold", "planned_km": 10, "adjusted_km": 10,
                "phase": "Build", "weekly_planned_km": 20, "weekly_adjusted_km": 20},
        second: {"slot": "long", "type": "Long Run", "planned_km": 10, "adjusted_km": 10,
                 "phase": "Build", "weekly_planned_km": 20, "weekly_adjusted_km": 20, "peak_long_km": 10.5},
    }
    allowed, errors = ai.validate_proposals([
        {"session_date": first.isoformat(), "kind": "increase_volume", "new_km": 10.9},
        {"session_date": second.isoformat(), "kind": "increase_volume", "new_km": 10.5},
    ], upcoming)
    assert len(allowed) == 2 and not errors

    too_much, errors = ai.validate_proposals([
        {"session_date": first.isoformat(), "kind": "increase_volume", "new_km": 10.9},
        {"session_date": second.isoformat(), "kind": "increase_volume", "new_km": 10.6},
    ], upcoming)
    assert len(too_much) == 1 and any("peak" in error for error in errors)

    upcoming[first]["phase"] = "Taper"
    too_late, errors = ai.validate_proposals([
        {"session_date": first.isoformat(), "kind": "increase_volume", "new_km": 10.1}
    ], upcoming)
    assert not too_late and any("taper" in error for error in errors)

    upcoming[first]["phase"] = "Build"
    upcoming[first]["weekly_adjusted_km"] = 22
    over_week, errors = ai.validate_proposals([
        {"session_date": first.isoformat(), "kind": "increase_volume", "new_km": 10.1}
    ], upcoming)
    assert not over_week and any("weekly distance" in error for error in errors)


def test_ai_changes_apply_and_can_be_reverted(db):
    with session_scope() as s:
        user = ps.ensure_user(s)
        run = next(item for item in ps.all_runs(s, user)
                   if item.date >= date.today() and item.slot not in {"race", "long"})
        upcoming = ai.upcoming_sessions(s, user, date.today())
        target = round(run.planned_km * 1.05, 1)
        proposal = ai.AIProposal(
            session_date=run.date, kind="increase_volume", new_km=target, reason="Gradual, capped increase"
        )
        assert ai.save_proposals(s, user, [proposal], upcoming) == 1
        applied = ps.list_adjustments(s, user)[0]
        assert applied.status == "applied"
        assert run.adjusted_km == target
        ai.set_status(s, user, applied.id, "reverted", today=START)
        assert run.adjusted_km <= run.planned_km


def test_accepted_friends_can_edit_future_runs_only(db):
    with session_scope() as s:
        owner = ps.ensure_user(s, auth_uid="shared-owner", email="owner@example.test")
        friend = ps.ensure_user(s, auth_uid="shared-friend", email="friend@example.test")
        stranger = ps.ensure_user(s, auth_uid="shared-stranger", email="stranger@example.test")
        cfg = PlanSettings(plan_start=monday_of(date.today()), horizon_weeks=26)
        for account in (owner, friend, stranger):
            ps.save_settings(s, account, cfg)
            ps.sync_plan(s, account, cfg)

        ps.send_friend_request(s, owner, friend.email)
        incoming, _ = ps.friend_requests(s, friend)
        assert len(incoming) == 1
        ps.respond_friend_request(s, friend, incoming[0].id, True)
        assert [account.id for account in ps.accepted_friends(s, owner)] == [friend.id]

        run = next(item for item in ps.all_runs(s, owner) if item.date >= date.today() and item.slot != "race")
        with pytest.raises(ValueError, match="accepted friend"):
            ps.edit_shared_run(s, stranger, owner, run.id, run.adjusted_km, run.run_type)
        target = round(run.adjusted_km, 1)
        ps.edit_shared_run(s, friend, owner, run.id, target, run.run_type)
        change = s.scalars(select(AIAdjustment).where(
            AIAdjustment.user_id == owner.id, AIAdjustment.created_by_user_id == friend.id
        )).first()
        assert change is not None and change.kind == "friend_edit"


def test_ai_context_includes_strength_progression(db):
    with session_scope() as s:
        user = ps.ensure_user(s)
        s.add(StrengthLog(user_id=user.id, date=START, exercise="Squat", weight_kg=60, sets=3, reps=8))
        history = ai.strength_history(s, user, START + timedelta(days=1))
        assert history == [{
            "exercise": "Squat", "sessions": 1,
            "latest": {"date": START.isoformat(), "weight_kg": 60, "sets": 3, "reps_per_set": 8,
                       "estimated_1rm_kg": 76.0},
            "best_estimated_1rm_kg": 76.0,
        }]


def test_garmin_credentials_are_encrypted_and_account_scoped(db, monkeypatch):
    cipher = Fernet(Fernet.generate_key())
    monkeypatch.setattr(garmin_accounts, "_cipher", lambda: cipher)
    with session_scope() as s:
        owner = ps.ensure_user(s, auth_uid="garmin-owner", email="garmin-owner@example.test")
        other = ps.ensure_user(s, auth_uid="garmin-other", email="garmin-other@example.test")
        garmin_accounts.save_credentials(s, owner, "runner@example.test", "private-password")
        rows = garmin_accounts._rows(s, owner)
        assert all("private-password" not in row.value and "runner@example.test" not in row.value
                   for row in rows.values())
        assert garmin_accounts.credentials(s, owner) == ("runner@example.test", "private-password")
        backup = ex.to_json(s, owner)
        assert "garmin_email_enc" not in backup and "garmin_password_enc" not in backup
        assert "runner@example.test" not in backup and "private-password" not in backup
        ex.restore_json(s, backup, owner)
        assert garmin_accounts.credentials(s, owner) == ("runner@example.test", "private-password")
        assert garmin_accounts.credentials(s, other) is None
        garmin_accounts.remove_credentials(s, owner)
        assert garmin_accounts.credentials(s, owner) is None
