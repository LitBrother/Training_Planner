from datetime import date

import pytest
from sqlalchemy import create_engine, inspect, text

from database.session import configure, session_scope
from models.orm import RunningSession
from services import garmin_link, plan_service as ps


def _first_run(s, user):
    return ps.all_runs(s, user)[0]


@pytest.mark.parametrize(
    ("duration", "rpe"),
    [(None, None), (37.5, None), (None, 6), (37.5, 6), (20, 1), (50.5, 10)],
)
def test_manual_run_context_saves_loads_and_does_not_change_run_totals(db, duration, rpe):
    with session_scope() as s:
        user = ps.ensure_user(s)
        run = _first_run(s, user)
        week = ps.list_weeks(s, user)[0]
        actual_km = round(run.adjusted_km, 1)
        ps.log_run(s, user, run.id, "completed", actual_km, "Easy effort", today=week.week_start)
        totals_before_context = ps.weekly_activity_summary(s, user, week)
        run_id = run.id

        ps.log_run(
            s, user, run.id, "completed", actual_km, "Easy effort", today=week.week_start,
            manual_duration_min=duration, perceived_effort_rpe=rpe,
        )
        totals_after_context = ps.weekly_activity_summary(s, user, week)

        assert totals_after_context == totals_before_context

    with session_scope() as s:
        run = s.get(RunningSession, run_id)
        assert run.manual_duration_min == duration
        assert run.perceived_effort_rpe == rpe
        assert run.status == "completed"
        assert run.actual_km == actual_km
        assert run.notes == "Easy effort"


def test_omitted_manual_context_preserves_existing_values(db):
    with session_scope() as s:
        user = ps.ensure_user(s)
        run = _first_run(s, user)
        run.manual_duration_min = 38.25
        run.perceived_effort_rpe = 7
        ps.log_run(s, user, run.id, "completed", 5.0, "Same run")
        assert run.manual_duration_min == 38.25
        assert run.perceived_effort_rpe == 7


@pytest.mark.parametrize("duration", [0, -1, float("inf"), float("-inf"), float("nan"), True, "30"])
def test_manual_duration_rejects_invalid_values_without_changing_run(db, duration):
    with session_scope() as s:
        user = ps.ensure_user(s)
        run = _first_run(s, user)
        ps.log_run(
            s, user, run.id, "completed", 4.5, "Existing notes",
            manual_duration_min=30, perceived_effort_rpe=5,
        )
        with pytest.raises(ValueError, match="positive finite"):
            ps.log_run(
                s, user, run.id, "missed", None, "Changed notes",
                manual_duration_min=duration, perceived_effort_rpe=8,
            )
        assert run.status == "completed"
        assert run.actual_km == 4.5
        assert run.notes == "Existing notes"
        assert run.manual_duration_min == 30
        assert run.perceived_effort_rpe == 5


@pytest.mark.parametrize("rpe", [0, 11, 1.5, float("inf"), float("nan"), True, "7"])
def test_perceived_effort_rejects_invalid_values_without_changing_run(db, rpe):
    with session_scope() as s:
        user = ps.ensure_user(s)
        run = _first_run(s, user)
        ps.log_run(
            s, user, run.id, "completed", 4.5, "Existing notes",
            manual_duration_min=30, perceived_effort_rpe=5,
        )
        with pytest.raises(ValueError, match="whole number from 1 to 10"):
            ps.log_run(
                s, user, run.id, "missed", None, "Changed notes",
                manual_duration_min=35, perceived_effort_rpe=rpe,
            )
        assert run.status == "completed"
        assert run.actual_km == 4.5
        assert run.notes == "Existing notes"
        assert run.manual_duration_min == 30
        assert run.perceived_effort_rpe == 5


def test_garmin_link_does_not_set_or_overwrite_manual_run_context(db):
    with session_scope() as s:
        user = ps.ensure_user(s)
        run, unrecorded_run = ps.all_runs(s, user)[:2]
        run.manual_duration_min = 41.75
        run.perceived_effort_rpe = 8

        garmin_link.link(s, user, run.id, {
            "id": "provider-run-id", "distance_km": 7.25, "duration_min": 39.0,
        })
        garmin_link.link(s, user, unrecorded_run.id, {
            "id": "provider-run-id-2", "distance_km": 4.25, "duration_min": 22.0,
        })

        assert run.manual_duration_min == 41.75
        assert run.perceived_effort_rpe == 8
        assert run.status == "completed"
        assert run.actual_km == 7.25
        assert run.source == "garmin"
        assert unrecorded_run.manual_duration_min is None
        assert unrecorded_run.perceived_effort_rpe is None


def test_legacy_sqlite_running_sessions_add_manual_context_columns(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}"
    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL, "
            "created_at DATETIME NOT NULL)"
        ))
        conn.execute(text(
            "INSERT INTO users (id, name, created_at) VALUES (1, 'Legacy', '2026-10-12 00:00:00')"
        ))
        conn.execute(text(
            "CREATE TABLE running_sessions ("
            "id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, week_id INTEGER, date DATE NOT NULL, "
            "weekday INTEGER NOT NULL DEFAULT 0, slot VARCHAR(20) NOT NULL DEFAULT '', "
            "name VARCHAR(100) NOT NULL DEFAULT '', run_type VARCHAR(40) NOT NULL DEFAULT 'Easy', "
            "variant VARCHAR(40) NOT NULL DEFAULT '', adjusted_type VARCHAR(40), "
            "planned_km FLOAT NOT NULL DEFAULT 0, adjusted_km FLOAT NOT NULL DEFAULT 0, actual_km FLOAT, "
            "status VARCHAR NOT NULL DEFAULT 'planned', notes TEXT, garmin_id VARCHAR, source VARCHAR, "
            "manual_lock BOOLEAN NOT NULL DEFAULT 0)"
        ))
        conn.execute(text(
            "INSERT INTO running_sessions "
            "(id, user_id, date, weekday, slot, name, run_type, planned_km, adjusted_km, actual_km, "
            "status, notes, source) VALUES "
            "(1, 1, '2026-10-12', 0, 'easy', 'Legacy run', 'Easy', 5, 5, 4.8, "
            "'completed', 'kept note', 'manual')"
        ))
    engine.dispose()

    configure(database_url)
    with session_scope() as s:
        columns = {column["name"] for column in inspect(s.get_bind()).get_columns("running_sessions")}
        run = s.get(RunningSession, 1)
        assert {"manual_duration_min", "perceived_effort_rpe"} <= columns
        assert run.date == date(2026, 10, 12)
        assert run.actual_km == 4.8
        assert run.notes == "kept note"
        assert run.manual_duration_min is None
        assert run.perceived_effort_rpe is None
