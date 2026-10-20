from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import delete
from streamlit.testing.v1 import AppTest

from database.session import session_scope
from models.orm import RunningSession, StrengthLog, StrengthSession, TrainingWeek, User
from services import auth, plan_service as ps
from tests.conftest import START
from utils.charts import weekly_volume_fig


def test_week_totals_keep_running_only_completion_semantics(db):
    with session_scope() as s:
        user = ps.ensure_user(s)
        week = ps.list_weeks(s, user)[0]
        runs = ps.week_runs(s, week.id)
        completed, missed = runs[:2]
        completed_actual = completed.adjusted_km / 2

        ps.log_run(s, user, completed.id, "completed", completed_actual, today=START)
        ps.log_run(s, user, missed.id, "missed", None, today=START)

        assert missed.actual_km == 0
        assert sum(run.planned_km for run in runs) == week.planned_km
        assert sum(run.adjusted_km for run in runs) == week.adjusted_km
        assert week.actual_km == completed_actual
        assert week.completion_pct == ps.week_completion(runs)
        assert week.completion_pct == round(completed_actual / week.adjusted_km * 100, 1)
        assert all(run.actual_km is None for run in runs[2:])


def test_running_completion_zero_adjusted_and_over_100_cap():
    assert ps.week_completion([SimpleNamespace(adjusted_km=0, actual_km=7)]) == 0
    assert ps.week_completion([SimpleNamespace(adjusted_km=5, actual_km=8)]) == 100


def test_garmin_fallback_is_chart_only_not_weekly_table_value():
    weekly = pd.DataFrame([{
        "week_start": date(2026, 10, 12),
        "planned": 20.0,
        "adjusted": 18.0,
        "actual": 0.0,
    }])
    chart = weekly_volume_fig(weekly, [{"date": "2026-10-14", "distance_km": 12.5}])

    actual = next(trace for trace in chart.data if trace.name == "Actual (Garmin or manual)")
    assert list(actual.y) == [12.5]
    assert weekly.loc[0, "actual"] == 0.0


def _run_page(monkeypatch, page_name):
    import streamlit as st
    from utils import bootstrap

    monkeypatch.setattr(bootstrap, "ready", lambda: None)
    monkeypatch.setattr(auth, "runtime_config", lambda: (None, None, None))
    page = Path(__file__).resolve().parent.parent / "pages" / page_name
    return AppTest.from_file(page).run()


def test_weekly_metrics_labels_and_completion_definition_in_ui(db, monkeypatch):
    completion_copy = "Running completion = Actual ÷ Adjusted (capped at 100%; 0% when Adjusted is 0)."
    expected_metrics = {
        "Planned running (km)",
        "Adjusted running (km)",
        "Actual running (km)",
        "Running completion (%)",
    }

    dashboard = _run_page(monkeypatch, "dashboard.py")
    assert not dashboard.exception
    assert expected_metrics.issubset({metric.label for metric in dashboard.metric})
    assert completion_copy in {caption.value for caption in dashboard.caption}

    planner = _run_page(monkeypatch, "training_plan.py")
    assert not planner.exception
    assert expected_metrics.issubset({metric.label for metric in planner.metric})
    assert completion_copy in {caption.value for caption in planner.caption}

    progress = _run_page(monkeypatch, "progress.py")
    assert not progress.exception
    progress_copy = " ".join(caption.value for caption in progress.caption)
    assert "Completion (%) = Actual ÷ Adjusted" in progress_copy
    assert "Actual may fall back to Garmin weekly totals" in progress_copy
    assert {
        "Planned running (km)",
        "Adjusted running (km)",
        "Actual running (km)",
        "Completion (%)",
    }.issubset(set(progress.dataframe[0].value.columns))


def test_weekly_activity_summary_running_only_distinguishes_recorded_statuses(db):
    with session_scope() as s:
        user = ps.ensure_user(s)
        week = ps.list_weeks(s, user)[0]
        s.execute(delete(StrengthSession).where(StrengthSession.user_id == user.id))
        runs = ps.week_runs(s, week.id, user)
        completed, missed = runs[:2]
        actual = completed.adjusted_km / 2
        ps.log_run(s, user, completed.id, "completed", actual, today=START)
        ps.log_run(s, user, missed.id, "missed", None, today=START)

        summary = ps.weekly_activity_summary(s, user, week)

        assert summary["has_running_records"]
        assert summary["completed_runs"] == 1
        assert summary["missed_runs"] == 1
        assert summary["planned_runs"] == len(runs) - 2
        assert summary["actual_running_km"] == actual
        assert summary["running_completion_pct"] == ps.week_completion(runs)
        assert summary["planned_strength_sessions"] == 0
        assert summary["lift_log_entries"] == 0


def test_weekly_activity_summary_strength_only_does_not_invent_run_totals(db):
    with session_scope() as s:
        user = ps.ensure_user(s)
        week = ps.list_weeks(s, user)[0]
        s.execute(delete(RunningSession).where(RunningSession.user_id == user.id))
        strengths = ps.week_strength(s, week.id, user)
        ps.log_strength(s, user, strengths[0].id, True)

        summary = ps.weekly_activity_summary(s, user, week)

        assert summary["has_running_records"] is False
        assert summary["running_completion_pct"] is None
        assert summary["planned_running_km"] is None
        assert summary["adjusted_running_km"] is None
        assert summary["actual_running_km"] is None
        assert summary["planned_strength_sessions"] == len(strengths)
        assert summary["completed_strength_sessions"] == 1


def test_weekly_activity_summary_keeps_lift_logs_distinct_from_planned_sessions(db):
    with session_scope() as s:
        user = ps.ensure_user(s)
        week = ps.list_weeks(s, user)[0]
        run = ps.week_runs(s, week.id, user)[0]
        strength = ps.week_strength(s, week.id, user)
        ps.log_run(s, user, run.id, "completed", run.adjusted_km, today=START)
        ps.log_strength(s, user, strength[0].id, True)
        s.add(StrengthLog(
            user_id=user.id, date=week.week_start, exercise="Squat",
            weight_kg=40, sets=3, reps=8,
        ))

        summary = ps.weekly_activity_summary(s, user, week)

        assert summary["has_running_records"]
        assert summary["completed_runs"] == 1
        assert summary["planned_strength_sessions"] == len(strength)
        assert summary["completed_strength_sessions"] == 1
        assert summary["lift_log_entries"] == 1


def test_weekly_activity_summary_scopes_runs_strength_and_lift_logs_to_user(db):
    with session_scope() as s:
        user = ps.ensure_user(s)
        week = ps.list_weeks(s, user)[0]
        other = User(name="Other athlete", auth_uid="other-athlete")
        s.add(other)
        s.flush()
        s.add(RunningSession(
            user_id=other.id, week_id=week.id, date=week.week_start, status="completed",
            planned_km=100, adjusted_km=100, actual_km=100,
        ))
        s.add(StrengthSession(
            user_id=other.id, week_id=week.id, date=week.week_start, kind="Legs", completed=True,
        ))
        s.add(StrengthLog(
            user_id=other.id, date=week.week_start, exercise="Deadlift",
            weight_kg=200, sets=5, reps=5,
        ))

        summary = ps.weekly_activity_summary(s, user, week)
        own_runs = ps.week_runs(s, week.id, user)
        own_strength = ps.week_strength(s, week.id, user)

        assert summary["planned_running_km"] == sum(run.planned_km for run in own_runs)
        assert summary["planned_strength_sessions"] == len(own_strength)
        assert summary["completed_strength_sessions"] == sum(item.completed for item in own_strength)
        assert summary["lift_log_entries"] == 0
        assert all(run.user_id == user.id for run in own_runs)
        assert all(item.user_id == user.id for item in own_strength)

        with pytest.raises(ValueError, match="Training week not found"):
            ps.weekly_activity_summary(s, other, week)


def test_weekly_planner_does_not_show_zero_running_totals_without_run_records(db, monkeypatch):
    with session_scope() as s:
        user = ps.ensure_user(s)
        s.execute(delete(RunningSession).where(RunningSession.user_id == user.id))

    planner = _run_page(monkeypatch, "training_plan.py")

    assert not planner.exception
    assert any("No running-session records" in info.value for info in planner.info)
    assert not any(metric.label == "Actual running (km)" for metric in planner.metric)
    assert not any(metric.label == "Running completion (%)" for metric in planner.metric)


def test_weekly_planner_displays_separate_strength_counts_including_zeroes(db, monkeypatch):
    with session_scope() as s:
        user = ps.ensure_user(s)
        s.execute(delete(StrengthSession).where(StrengthSession.user_id == user.id))
        s.execute(delete(StrengthLog).where(StrengthLog.user_id == user.id))

    planner = _run_page(monkeypatch, "training_plan.py")

    assert not planner.exception
    strength_metrics = {
        metric.label: metric.value
        for metric in planner.metric
        if metric.label in {
            "Planned strength sessions",
            "Completed planned strength sessions",
            "Lift-log entries",
        }
    }
    assert strength_metrics == {
        "Planned strength sessions": "0",
        "Completed planned strength sessions": "0",
        "Lift-log entries": "0",
    }
    assert any(
        "exercise-level records from the Strength page" in caption.value
        and "not added to or included in either scheduled-session count" in caption.value
        for caption in planner.caption
    )
