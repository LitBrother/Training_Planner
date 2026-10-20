from datetime import date
import json

import pytest
from sqlalchemy import create_engine, inspect, text

from database.session import session_scope
from database.session import configure
from services import ai_service as ai
from services import export_service as ex
from services import plan_service as ps
from services.adjustments import adjust_km, completion_pct, reduction_factor
from services.plan_generator import generate_plan
from models.orm import StrengthLog
from tests.conftest import START


def _slot(s, week, slot):
    return next(r for r in ps.week_runs(s, week.id) if r.slot == slot)


def test_missed_long_run_not_made_up(db):
    with session_scope() as s:
        u = ps.ensure_user(s)
        w1, w2 = ps.list_weeks(s, u)[:2]
        long1 = _slot(s, w1, "long")
        ps.log_run(s, u, long1.id, "missed", None, "", today=date(2026, 10, 19))
        assert _slot(s, w2, "long").adjusted_km <= long1.planned_km
        assert all(r.adjusted_km <= r.planned_km + 1e-9 for r in ps.all_runs(s, u))


def test_missed_quality_not_moved_or_added(db):
    with session_scope() as s:
        u = ps.ensure_user(s)
        w1, w2 = ps.list_weeks(s, u)[:2]
        before = len(ps.all_runs(s, u))
        for r in ps.week_runs(s, w1.id):
            ps.log_run(s, u, r.id, "missed" if r.slot == "quality" else "completed", None, "",
                       today=date(2026, 10, 19))
        q = _slot(s, w1, "quality")
        assert q.date == date(2026, 10, 13) and q.status == "missed"
        assert len(ps.all_runs(s, u)) == before
        assert w2.adjusted_km <= w2.planned_km
        assert _slot(s, w2, "quality").adjusted_km <= _slot(s, w2, "quality").planned_km


def test_half_marathon_week(db):
    with session_scope() as s:
        u = ps.ensure_user(s)
        ps.add_event(s, u, "Local Half", "Half marathon", date(2026, 11, 14), 21.1, "B", today=START)
        w = ps.list_weeks(s, u)[4]
        runs = ps.week_runs(s, w.id)
        assert [r.weekday for r in runs] == [1, 4, 5]
        quality, long_, weekend = runs
        assert quality.adjusted_km == round(quality.planned_km * 0.6, 1)
        assert long_.adjusted_type == "Shakeout" and long_.adjusted_km <= 5
        assert weekend.adjusted_type == "Event" and weekend.adjusted_km == 21.1


def test_taper_protection(db):
    plan = generate_plan(START, date(2027, 9, 26), 32, 14, 3)
    taper = [w for w in plan if w["phase"] == "Taper"]
    assert len(taper) == 3 and plan[-1]["phase"] == "Race Week"
    totals = [sum(r["km"] for r in w["runs"]) for w in taper]
    assert totals == sorted(totals, reverse=True)
    peak_week = [w for w in plan if w["phase"] == "Peak"][-1]
    assert totals[0] < sum(r["km"] for r in peak_week["runs"])
    assert all(x["volume_level"] == "minimal" for w in taper for x in w["strength"] if x["kind"] == "Legs")
    with session_scope() as s:
        u = ps.ensure_user(s)
        ps.recalc(s, u, today=date(2027, 9, 1))
        weeks = ps.list_weeks(s, u)
        for w in weeks:
            if w.phase == "Taper":
                assert w.adjusted_km <= w.planned_km
    t = next(w for w in plan if w["phase"] == "Taper")["runs"][0]
    up = {t["date"]: {"slot": t["slot"], "type": t["run_type"], "adjusted_km": t["km"]}}
    ok, errs = ai.validate_proposals([{"session_date": t["date"].isoformat(), "kind": "reduce_volume",
                                       "new_km": t["km"] + 5}], up)
    assert not ok and errs


def test_ai_adjustment_validation():
    d = date(2026, 10, 20)
    up = {d: {"slot": "quality", "type": "Threshold", "adjusted_km": 8.0}}
    base = {"session_date": "2026-10-20", "kind": "reduce_volume", "new_km": 6.0}
    ok, errs = ai.validate_proposals([base], up)
    assert len(ok) == 1 and not errs
    for bad in ({**base, "new_km": 9.0}, {**base, "new_date": "2026-10-21"},
                {**base, "new_type": "Intervals"}, {**base, "session_date": "2026-10-25"}):
        ok, errs = ai.validate_proposals([bad], up)
        assert not ok and errs
    ok, _ = ai.validate_proposals([{**base, "kind": "simplify_quality", "new_type": "Easy"}], up)
    assert len(ok) == 1


def test_backup_restore(db):
    with session_scope() as s:
        u = ps.ensure_user(s)
        ps.add_note(s, u, date(2026, 10, 13), "Sleep", "Slept badly")
        ps.add_event(s, u, "10k", "10km", date(2026, 12, 6), 10.0, "C", today=START)
        backup = ex.to_json(s, u)
        n_runs = len(ps.all_runs(s, u))
    with session_scope() as s:
        u = ps.ensure_user(s)
        for n in ps.list_notes(s, u):
            ps.delete_note(s, n.id)
        for e in ps.list_events(s, u):
            ps.delete_event(s, u, e.id, today=START)
    with session_scope() as s:
        u = ps.ensure_user(s)
        legacy_backup = json.loads(backup)
        legacy_backup["tables"].pop("strength_logs")
        counts = ex.restore_json(s, json.dumps(legacy_backup), u)
        assert counts["notes"] == 1
        assert counts["strength_logs"] == 0
        assert len(ps.list_notes(s, u)) == 1
        assert any(e.name == "10k" for e in ps.list_events(s, u))
        assert len(ps.all_runs(s, u)) == n_runs


def test_authenticated_accounts_are_isolated(db):
    with session_scope() as s:
        owner = ps.ensure_user(s, auth_uid="auth-owner", email="owner@example.com")
        other = ps.ensure_user(s, auth_uid="auth-other", email="other@example.com")
        ps.add_note(s, owner, START, "Training", "Owner-only note")
        ps.add_note(s, other, START, "Training", "Other-account note")
        s.add(StrengthLog(user_id=owner.id, date=START, exercise="Squat", weight_kg=60, sets=3, reps=8))
        s.add(StrengthLog(user_id=other.id, date=START, exercise="Squat", weight_kg=100, sets=3, reps=5))
        run = ps.all_runs(s, owner)[0]
        with pytest.raises(ValueError, match="not found for this account"):
            ps.log_run(s, other, run.id, "missed", None)

        backup = ex.to_json(s, owner)
        payload = json.loads(backup)
        assert [n["text"] for n in payload["tables"]["notes"]] == ["Owner-only note"]
        assert [row["weight_kg"] for row in payload["tables"]["strength_logs"]] == [60]
        assert "auth_uid" not in payload["tables"]["users"][0]
        assert "email" not in payload["tables"]["users"][0]
        ps.add_note(s, owner, START, "General", "Remove when restoring")
        counts = ex.restore_json(s, backup, owner)
        assert counts["notes"] == 1

        assert [n.text for n in ps.list_notes(s, owner)] == ["Owner-only note"]
        assert [n.text for n in ps.list_notes(s, other)] == ["Other-account note"]
        owner_lifts = list(s.query(StrengthLog).filter_by(user_id=owner.id))
        other_lifts = list(s.query(StrengthLog).filter_by(user_id=other.id))
        assert [lift.weight_kg for lift in owner_lifts] == [60]
        assert [lift.weight_kg for lift in other_lifts] == [100]


def test_mileage_adjustment_calculations():
    assert reduction_factor(100) == 1.0 and reduction_factor(90) == 1.0
    assert reduction_factor(89.9) == 0.95 and reduction_factor(75) == 0.95
    assert reduction_factor(74.9) == 0.9 and reduction_factor(0) == 0.9
    assert adjust_km(20, 0.95) == 19.0 and adjust_km(26, 0.9) == 23.4
    assert completion_pct(25, 20) == 80.0 and completion_pct(0, 5) == 0.0 and completion_pct(10, 12) == 100.0


def test_schema_migration_adds_auth_columns(tmp_path):
    path = tmp_path / "legacy.db"
    database_url = f"sqlite:///{path.as_posix()}"
    engine = create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL, "
            "created_at DATETIME NOT NULL)"
        ))
    engine.dispose()

    configure(database_url)
    with session_scope() as s:
        columns = {column["name"] for column in inspect(s.get_bind()).get_columns("users")}
        indexes = {index["name"] for index in inspect(s.get_bind()).get_indexes("users")}
        bind = s.get_bind()
    assert {"auth_uid", "email"} <= columns
    assert "uq_users_auth_uid" in indexes
    bind.dispose()