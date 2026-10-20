from pathlib import Path

from sqlalchemy import delete, select
from streamlit.testing.v1 import AppTest

from database.session import session_scope
from models.orm import (
    AIAdjustment,
    AthleteProfile,
    Event,
    Note,
    RunningSession,
    Setting,
    StrengthLog,
    StrengthSession,
    TrainingWeek,
    User,
)
from services import auth, plan_service as ps


ACCOUNT_MODELS = (
    AthleteProfile,
    Setting,
    TrainingWeek,
    RunningSession,
    StrengthSession,
    StrengthLog,
    Event,
    Note,
    AIAdjustment,
)


def _account_snapshot():
    with session_scope() as s:
        user = ps.ensure_user(s)
        snapshot = {
            User.__name__: tuple(
                getattr(user, column.name) for column in User.__table__.columns
            )
        }
        for model in ACCOUNT_MODELS:
            rows = s.scalars(select(model).where(model.user_id == user.id)).all()
            snapshot[model.__name__] = tuple(
                tuple(getattr(row, column.name) for column in model.__table__.columns)
                for row in rows
            )
        return snapshot


def _run_dashboard(monkeypatch):
    import streamlit as st
    from utils import bootstrap

    monkeypatch.setattr(bootstrap, "ready", lambda: None)
    monkeypatch.setattr(auth, "runtime_config", lambda: (None, None, None))
    destinations = []
    monkeypatch.setattr(st, "switch_page", destinations.append)
    dashboard = Path(__file__).resolve().parent.parent / "pages" / "dashboard.py"
    app = AppTest.from_file(dashboard).run()
    return app, destinations


def test_empty_dashboard_action_routes_to_settings_without_mutating_account(db, monkeypatch):
    with session_scope() as s:
        user = ps.ensure_user(s)
        s.execute(delete(RunningSession).where(RunningSession.user_id == user.id))
        s.execute(delete(StrengthSession).where(StrengthSession.user_id == user.id))
        s.execute(delete(TrainingWeek).where(TrainingWeek.user_id == user.id))
        assert ps.dashboard_summary(s, user) is None

    before = _account_snapshot()
    app, destinations = _run_dashboard(monkeypatch)

    assert not app.exception
    assert any("No training plan found" in warning.value for warning in app.warning)
    action = next(button for button in app.button if button.label == "Open plan setup in Settings")
    action.click().run()

    assert not app.exception
    assert destinations == ["pages/settings.py"]
    assert _account_snapshot() == before


def test_plan_present_dashboard_remains_unchanged(db, monkeypatch):
    before = _account_snapshot()
    app, destinations = _run_dashboard(monkeypatch)

    assert not app.exception
    assert not any("No training plan found" in warning.value for warning in app.warning)
    assert not any(button.label == "Open plan setup in Settings" for button in app.button)
    assert any(metric.label == "Planned running (km)" for metric in app.metric)
    assert destinations == []
    assert _account_snapshot() == before
