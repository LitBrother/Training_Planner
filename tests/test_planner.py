from datetime import date

from database.session import session_scope
from services import ai_service as ai
from services import export_service as ex
from services import plan_service as ps
from services.adjustments import adjust_km, completion_pct, reduction_factor
from services.plan_generator import generate_plan
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
        backup = ex.to_json(s)
        n_runs = len(ps.all_runs(s, u))
    with session_scope() as s:
        u = ps.ensure_user(s)
        for n in ps.list_notes(s, u):
            ps.delete_note(s, n.id)
        for e in ps.list_events(s, u):
            ps.delete_event(s, u, e.id, today=START)
    with session_scope() as s:
        counts = ex.restore_json(s, backup)
        u = ps.ensure_user(s)
        assert counts["notes"] == 1
        assert len(ps.list_notes(s, u)) == 1
        assert any(e.name == "10k" for e in ps.list_events(s, u))
        assert len(ps.all_runs(s, u)) == n_runs


def test_mileage_adjustment_calculations():
    assert reduction_factor(100) == 1.0 and reduction_factor(90) == 1.0
    assert reduction_factor(89.9) == 0.95 and reduction_factor(75) == 0.95
    assert reduction_factor(74.9) == 0.9 and reduction_factor(0) == 0.9
    assert adjust_km(20, 0.95) == 19.0 and adjust_km(26, 0.9) == 23.4
    assert completion_pct(25, 20) == 80.0 and completion_pct(0, 5) == 0.0 and completion_pct(10, 12) == 100.0