from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from streamlit.testing.v1 import AppTest

from database.session import session_scope
from models.orm import RunningSession, Setting, TrainingWeek, User
from services import plan_service as ps


def _plan_snapshot(s, user):
    runs = s.scalars(
        select(RunningSession).where(RunningSession.user_id == user.id).order_by(RunningSession.date)
    ).all()
    weeks = s.scalars(
        select(TrainingWeek).where(TrainingWeek.user_id == user.id).order_by(TrainingWeek.week_start)
    ).all()
    return (
        tuple((run.id, run.date, run.status, run.actual_km, run.planned_km, run.adjusted_km) for run in runs),
        tuple((week.id, week.week_start, week.planned_km, week.adjusted_km) for week in weeks),
    )


def test_pause_resume_persists_without_shifting_or_adding_sessions_across_week_boundary(db):
    with session_scope() as s:
        user = ps.ensure_user(s)
        first_week, next_week = ps.list_weeks(s, user)[:2]
        missed = next(run for run in ps.week_runs(s, first_week.id, user) if run.slot == "long")

        ps.log_run(s, user, missed.id, "missed", None, today=next_week.week_start)
        assert missed.date < next_week.week_start
        assert missed.status == "missed" and missed.actual_km == 0
        next_long = next(run for run in ps.week_runs(s, next_week.id, user) if run.slot == "long")
        assert next_long.adjusted_km <= missed.planned_km
        before = _plan_snapshot(s, user)

        ps.set_training_paused(s, user, True)
        ps.set_training_paused(s, user, True)
        assert ps.training_is_paused(s, user)
        ps.recalc(s, user, today=next_week.week_start)
        assert _plan_snapshot(s, user)[0] == before[0]
        assert next_long.adjusted_km <= missed.planned_km

        ps.set_training_paused(s, user, False)
        ps.set_training_paused(s, user, False)
        assert not ps.training_is_paused(s, user)
        assert _plan_snapshot(s, user) == before

        rows = s.scalars(select(Setting).where(
            Setting.user_id == user.id, Setting.key == "training_paused"
        )).all()
        assert rows == []

    with session_scope() as s:
        user = ps.ensure_user(s)
        assert not ps.training_is_paused(s, user)
        final_runs, final_weeks = _plan_snapshot(s, user)
        assert len(final_runs) == len(before[0])
        assert len(final_weeks) == len(before[1])


def test_pending_pause_ui_state_is_cleared_when_account_changes(db, monkeypatch):
    from services import auth
    from utils import bootstrap

    monkeypatch.setattr(bootstrap, "ready", lambda: None)
    monkeypatch.setattr(
        auth, "runtime_config",
        lambda: ("test-database-url", "https://example.invalid", "test-public-key"),
    )
    identity = {"uid": "pause-ui-account-one"}
    monkeypatch.setattr(
        auth, "current_identity",
        lambda _key: (identity["uid"], f'{identity["uid"]}@example.test'),
    )
    planner = Path(__file__).resolve().parent.parent / "pages" / "training_plan.py"
    app = AppTest.from_file(planner).run()
    assert not app.exception
    first_identity = app.session_state["_training_pause_identity"]

    app.session_state["training_pause_confirmation"] = "pause"
    app.session_state["training_pause_notice"] = "Training paused for the previous account."
    identity["uid"] = "pause-ui-account-two"
    with session_scope() as s:
        user = ps.ensure_user(
            s, auth_uid=identity["uid"], email=f'{identity["uid"]}@example.test',
        )
        ps.ensure_defaults(s, user)
        ps.sync_plan(s, user, ps.get_settings(s, user))
    app.run()

    assert not app.exception
    assert app.session_state["_training_pause_identity"] != first_identity
    assert "training_pause_confirmation" not in app.session_state
    assert "training_pause_notice" not in app.session_state
    assert not any(button.label == "Confirm pause training" for button in app.button)
    assert not any("previous account" in item.value for item in app.success)


def test_pause_state_is_account_scoped_persistent_and_idempotent(db):
    with session_scope() as s:
        owner = ps.ensure_user(s, auth_uid="pause-owner", email="owner@example.com")
        other = ps.ensure_user(s, auth_uid="pause-other", email="other@example.com")
        assert owner.id != other.id
        assert not ps.training_is_paused(s, owner)
        assert not ps.training_is_paused(s, other)
        owner_id, other_id = owner.id, other.id

        ps.set_training_paused(s, owner, True)
        ps.set_training_paused(s, owner, True)
        assert ps.training_is_paused(s, owner)
        assert not ps.training_is_paused(s, other)

    with session_scope() as s:
        owner = s.get(User, owner_id)
        other = s.get(User, other_id)
        assert ps.training_is_paused(s, owner)
        assert not ps.training_is_paused(s, other)


def test_pause_state_rejects_non_boolean_values(db):
    with session_scope() as s:
        user = ps.ensure_user(s)
        with pytest.raises(ValueError, match="must be true or false"):
            ps.set_training_paused(s, user, 1)
        assert not ps.training_is_paused(s, user)


def test_pause_resume_confirmation_and_cancel_controls(db, monkeypatch):
    from services import auth
    from utils import bootstrap

    monkeypatch.setattr(bootstrap, "ready", lambda: None)
    monkeypatch.setattr(auth, "runtime_config", lambda: (None, None, None))
    planner = Path(__file__).resolve().parent.parent / "pages" / "training_plan.py"
    app = AppTest.from_file(planner).run()
    assert not app.exception

    next(button for button in app.button if button.label == "Pause training").click().run()
    assert not app.exception
    assert any("Pausing does not shift calendar dates" in item.value for item in app.info)
    next(button for button in app.button if button.label == "Cancel").click().run()
    assert not app.exception
    with session_scope() as s:
        user = ps.ensure_user(s)
        assert not ps.training_is_paused(s, user)

    next(button for button in app.button if button.label == "Pause training").click().run()
    next(button for button in app.button if button.label == "Confirm pause training").click().run()
    assert not app.exception
    assert any("Training paused." in item.value for item in app.success)
    with session_scope() as s:
        user = ps.ensure_user(s)
        assert ps.training_is_paused(s, user)

    next(button for button in app.button if button.label == "Resume training").click().run()
    assert any("Resuming only clears the pause status" in item.value for item in app.info)
    next(button for button in app.button if button.label == "Cancel").click().run()
    with session_scope() as s:
        user = ps.ensure_user(s)
        assert ps.training_is_paused(s, user)

    next(button for button in app.button if button.label == "Resume training").click().run()
    next(button for button in app.button if button.label == "Confirm resume training").click().run()
    assert not app.exception
    assert any("Training resumed." in item.value for item in app.success)
    with session_scope() as s:
        user = ps.ensure_user(s)
        assert not ps.training_is_paused(s, user)

    def fail_pause(_session, _user, _paused):
        raise SQLAlchemyError("simulated persistence failure")

    monkeypatch.setattr(ps, "set_training_paused", fail_pause)
    next(button for button in app.button if button.label == "Pause training").click().run()
    next(button for button in app.button if button.label == "Confirm pause training").click().run()
    assert not app.exception
    assert any("Could not pause training" in item.value for item in app.error)
    with session_scope() as s:
        user = ps.ensure_user(s)
        assert not ps.training_is_paused(s, user)
