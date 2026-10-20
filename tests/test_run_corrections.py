import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, inspect, select, text

from database.session import configure, session_scope
from models.orm import RunCorrectionHistory, RunningSession, TrainingWeek, User
from services import export_service, plan_service as ps
from tests.conftest import START


def _completed_manual_run(s, user):
    run = ps.all_runs(s, user)[0]
    ps.log_run(
        s, user, run.id, "completed", 5.0, "Original note", today=START,
        manual_duration_min=32.5, perceived_effort_rpe=6,
    )
    return run


def test_correction_history_is_timestamped_owner_scoped_and_recalculated(db):
    with session_scope() as s:
        owner = ps.ensure_user(s)
        other = User(name="Other", auth_uid="other-account")
        s.add(other)
        s.flush()
        run = _completed_manual_run(s, owner)
        run_id = run.id
        week_id = run.week_id

        assert ps.correct_run_distance(s, owner, run_id, "actual_km", 6.25)
        assert ps.correct_run_distance(s, owner, run_id, "actual_km", 7.5)
        with pytest.raises(ValueError, match="not found for this account"):
            ps.correct_run_distance(s, other, run_id, "actual_km", 8.0)
        with pytest.raises(ValueError, match="not found for this account"):
            ps.run_correction_history(s, other, run_id)

        history = ps.run_correction_history(s, owner, run_id)
        assert [(entry.field_name, entry.old_value, entry.new_value) for entry in history] == [
            ("actual_km", 5.0, 6.25),
            ("actual_km", 6.25, 7.5),
        ]
        timestamp = history[-1].corrected_at
        timestamp_utc = (
            timestamp.replace(tzinfo=timezone.utc) if timestamp.tzinfo is None
            else timestamp.astimezone(timezone.utc)
        )
        now_utc = datetime.now(timezone.utc)
        assert abs((now_utc - timestamp_utc).total_seconds()) < 10

        assert run.status == "completed"
        assert run.actual_km == 7.5
        assert run.notes == "Original note"
        assert run.manual_duration_min == 32.5
        assert run.perceived_effort_rpe == 6
        summary = ps.weekly_activity_summary(s, owner, s.get(TrainingWeek, week_id))
        assert summary["actual_running_km"] == 7.5


def test_noop_and_standard_log_cannot_bypass_correction_history(db):
    with session_scope() as s:
        owner = ps.ensure_user(s)
        run = _completed_manual_run(s, owner)
        assert not ps.correct_run_distance(s, owner, run.id, "actual_km", 5.0)
        with pytest.raises(ValueError, match="correction flow"):
            ps.log_run(s, owner, run.id, "completed", 5.5, "Changed note", today=START)
        assert run.actual_km == 5.0
        assert run.notes == "Original note"
        assert ps.run_correction_history(s, owner, run.id) == []


def test_legacy_completed_unlinked_run_can_be_corrected_without_fake_prior_history(db):
    with session_scope() as s:
        owner = ps.ensure_user(s)
        run = _completed_manual_run(s, owner)
        run.source = None  # Older manually logged rows predate the source column.
        run_id = run.id

        assert ps.run_correction_history(s, owner, run_id) == []
        assert ps.correct_run_distance(s, owner, run_id, "actual_km", 5.5)
        history = ps.run_correction_history(s, owner, run_id)
        assert len(history) == 1
        assert (history[0].old_value, history[0].new_value) == (5.0, 5.5)


@pytest.mark.parametrize(
    "value",
    [0, -0.1, 100.1, float("nan"), float("inf"), float("-inf"), True, False, "6.0", None],
)
def test_invalid_correction_values_do_not_change_anything(db, value):
    with session_scope() as s:
        owner = ps.ensure_user(s)
        run = _completed_manual_run(s, owner)
        with pytest.raises(ValueError, match="positive finite"):
            ps.correct_run_distance(s, owner, run.id, "actual_km", value)
        assert run.actual_km == 5.0
        assert ps.run_correction_history(s, owner, run.id) == []


def test_correction_field_and_garmin_link_are_rejected(db):
    with session_scope() as s:
        owner = ps.ensure_user(s)
        run = _completed_manual_run(s, owner)
        with pytest.raises(ValueError, match="Only actual_km"):
            ps.correct_run_distance(s, owner, run.id, "notes", 6.0)
        run.garmin_id = "provider-id"
        run.source = "garmin"
        with pytest.raises(ValueError, match="Only completed, manually logged"):
            ps.correct_run_distance(s, owner, run.id, "actual_km", 6.0)
        assert run.actual_km == 5.0
        assert ps.run_correction_history(s, owner, run.id) == []


@pytest.mark.parametrize("failure_point", ["audit", "recalc"])
def test_failed_audit_or_recalculation_rolls_back_correction(db, monkeypatch, failure_point):
    with session_scope() as s:
        owner = ps.ensure_user(s)
        run = _completed_manual_run(s, owner)
        run_id = run.id
        week_id = run.week_id
        original_week_actual = s.get(TrainingWeek, week_id).actual_km

        if failure_point == "audit":
            def fail_audit(_mapper, _connection, _target):
                raise RuntimeError("simulated audit insert failure")

            event.listen(RunCorrectionHistory, "before_insert", fail_audit)
        else:
            def fail_recalc(*_args, **_kwargs):
                raise RuntimeError("simulated recalculation failure")

            monkeypatch.setattr(ps, "recalc", fail_recalc)

        try:
            with pytest.raises(RuntimeError, match="simulated"):
                ps.correct_run_distance(s, owner, run_id, "actual_km", 8.0)
        finally:
            if failure_point == "audit":
                event.remove(RunCorrectionHistory, "before_insert", fail_audit)

        s.expire_all()
        persisted_run = s.get(RunningSession, run_id)
        assert persisted_run.actual_km == 5.0
        assert s.get(TrainingWeek, week_id).actual_km == original_week_actual
        assert ps.run_correction_history(s, owner, run_id) == []


def test_json_export_restore_preserves_account_scoped_history_and_run_reference(db):
    with session_scope() as s:
        owner = ps.ensure_user(s)
        other = User(name="Other", auth_uid="other-account")
        s.add(other)
        s.flush()
        run = _completed_manual_run(s, owner)
        ps.correct_run_distance(s, owner, run.id, "actual_km", 6.0)
        other_run = RunningSession(
            user_id=other.id, date=START, weekday=0, slot="easy", name="Other",
            run_type="Easy", planned_km=5, adjusted_km=5, actual_km=4,
            status="completed", source="manual",
        )
        s.add(other_run)
        s.flush()
        other_history = RunCorrectionHistory(
            user_id=other.id, running_session_id=other_run.id, field_name="actual_km",
            corrected_at=datetime.now(timezone.utc), old_value=4, new_value=4.5,
        )
        s.add(other_history)
        s.flush()

        encoded = export_service.to_json(s, owner)
        backup = json.loads(encoded)
        assert len(backup["tables"]["run_correction_history"]) == 1
        assert backup["tables"]["run_correction_history"][0]["user_id"] == owner.id
        assert backup["tables"]["run_correction_history"][0]["old_value"] == 5.0
        assert backup["tables"]["run_correction_history"][0]["corrected_at"].endswith("+00:00")

        # Restoring replaces only the authenticated owner's rows and remaps the parent ID.
        counts = export_service.restore_json(s, encoded, owner)
        assert counts["run_correction_history"] == 1
        restored = ps.all_runs(s, owner)
        owner_runs = {item.id: item for item in restored}
        restored_history = ps.run_correction_history(s, owner, restored[0].id)
        assert len(restored_history) == 1
        assert restored_history[0].running_session_id in owner_runs
        assert restored_history[0].new_value == 6.0
        assert ps.run_correction_history(s, other, other_run.id)[0].new_value == 4.5
        assert len(export_service.dump_export(s, "json", owner)["run_correction_history"]) == 1


def test_legacy_restore_cannot_silently_discard_existing_correction_history(db):
    with session_scope() as s:
        owner = ps.ensure_user(s)
        run = _completed_manual_run(s, owner)
        ps.correct_run_distance(s, owner, run.id, "actual_km", 6.0)
        backup = json.loads(export_service.to_json(s, owner))
        backup["tables"].pop("run_correction_history")
        with pytest.raises(ValueError, match="backup that includes it"):
            export_service.restore_json(s, json.dumps(backup), owner)
        assert run.actual_km == 6.0
        assert len(ps.run_correction_history(s, owner, run.id)) == 1


def test_restore_rejects_run_correction_with_missing_parent_before_replacing_data(db):
    with session_scope() as s:
        owner = ps.ensure_user(s)
        run = _completed_manual_run(s, owner)
        ps.correct_run_distance(s, owner, run.id, "actual_km", 6.0)
        backup = json.loads(export_service.to_json(s, owner))
        backup["tables"]["run_correction_history"][0]["running_session_id"] = 999999

        with pytest.raises(ValueError, match="run correction history is invalid"):
            export_service.restore_json(s, json.dumps(backup), owner)
        assert run.actual_km == 6.0
        assert len(ps.run_correction_history(s, owner, run.id)) == 1


def test_legacy_database_migration_creates_empty_correction_history_table(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'legacy-corrections.db').as_posix()}"
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
            "planned_km FLOAT NOT NULL DEFAULT 0, adjusted_km FLOAT NOT NULL DEFAULT 0, "
            "actual_km FLOAT, status VARCHAR NOT NULL DEFAULT 'planned', notes TEXT, "
            "garmin_id VARCHAR, source VARCHAR, manual_lock BOOLEAN NOT NULL DEFAULT 0)"
        ))
        conn.execute(text(
            "INSERT INTO running_sessions "
            "(id, user_id, date, actual_km, status, source) "
            "VALUES (1, 1, '2026-10-12', 4.5, 'completed', 'manual')"
        ))
    engine.dispose()

    configure(database_url)
    with session_scope() as s:
        inspector = inspect(s.get_bind())
        assert inspector.has_table("run_correction_history")
        assert s.scalar(select(RunCorrectionHistory.id)) is None
        assert s.get(RunningSession, 1).actual_km == 4.5
