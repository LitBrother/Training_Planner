from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from database.session import session_scope
from models.orm import AIAdjustment, User
from services import ai_service as ai
from services import plan_service as ps
from tests.conftest import START


AI_PAGE = Path(__file__).resolve().parent.parent / "pages" / "ai_coach.py"


def _adjustment(user_id, created_at, status="applied", kind="increase_volume", **kwargs):
    values = {
        "user_id": user_id,
        "created_at": created_at,
        "session_date": START + timedelta(days=1),
        "kind": kind,
        "old_km": 5.0,
        "new_km": 4.5,
        "old_type": "Easy",
        "new_type": "Easy",
        "reason": "History test",
        "status": status,
    }
    values.update(kwargs)
    return AIAdjustment(**values)


def test_adjustment_history_is_owner_scoped_inclusive_and_unbounded(db):
    with session_scope() as s:
        owner = ps.ensure_user(s)
        other = User(name="Other account", auth_uid="history-other", email="history-other@example.test")
        s.add(other)
        s.flush()
        start = date(2026, 10, 20)
        end = date(2026, 10, 21)
        rows = [
            _adjustment(owner.id, datetime(2026, 10, 20, 0, 0), "applied"),
            _adjustment(owner.id, datetime(2026, 10, 21, 23, 59), "reverted"),
            _adjustment(owner.id, datetime(2026, 10, 22, 0, 0), "pending"),
            _adjustment(owner.id, datetime(2026, 10, 21, 12), "rejected"),
            _adjustment(owner.id, datetime(2026, 10, 21, 13), "approved"),
            _adjustment(other.id, datetime(2026, 10, 21, 14), "applied"),
        ]
        s.add_all(rows)
        s.flush()

        bounded = ps.list_adjustments(s, owner, start_date=start, end_date=end)
        combined = ps.list_adjustments(
            s, owner, status="reverted", start_date=start, end_date=end
        )
        unbounded = ps.list_adjustments(s, owner)

        assert [item.status for item in bounded] == ["reverted", "approved", "rejected", "applied"]
        assert combined == [rows[1]]
        assert all(item.user_id == owner.id for item in unbounded)
        assert unbounded[0].status == "pending"

        # There is no history cap: every owner record is returned.
        s.add_all(
            _adjustment(
                owner.id, datetime(2026, 11, 1) + timedelta(minutes=index), "pending"
            )
            for index in range(30)
        )
        s.flush()
        assert len(ps.list_adjustments(s, owner)) == len(rows) - 1 + 30


def test_revert_rejects_ineligible_and_foreign_records(db):
    with session_scope() as s:
        owner = ps.ensure_user(s)
        other = User(name="Other account", auth_uid="history-revert-other")
        s.add(other)
        s.flush()
        run = next(item for item in ps.all_runs(s, owner) if item.date >= START and item.slot != "race")
        entries = [
            _adjustment(owner.id, datetime(2026, 10, 20), status)
            for status in ("pending", "rejected", "reverted")
        ]
        foreign = _adjustment(other.id, datetime(2026, 10, 20), "applied")
        s.add_all([*entries, foreign])
        s.flush()

        for entry in entries:
            with pytest.raises(ValueError, match="Only an applied adjustment"):
                ai.set_status(s, owner, entry.id, "reverted", today=START)
        with pytest.raises(ValueError, match="not found for this account"):
            ai.set_status(s, owner, foreign.id, "reverted", today=START)


def test_reverting_latest_change_recalculates_to_another_active_change(db):
    with session_scope() as s:
        owner = ps.ensure_user(s)
        run = next(item for item in ps.all_runs(s, owner) if item.date >= START and item.slot != "race")
        older_value = round(run.planned_km - 0.5, 1)
        latest_value = round(run.planned_km - 1.0, 1)
        older = _adjustment(
            owner.id, datetime(2026, 10, 20, 10), "approved",
            session_date=run.date, old_km=run.planned_km, new_km=older_value,
            old_type=run.run_type, new_type=run.run_type,
        )
        latest = _adjustment(
            owner.id, datetime(2026, 10, 20, 11), "applied",
            session_date=run.date, old_km=run.planned_km, new_km=latest_value,
            old_type=run.run_type, new_type=run.run_type,
        )
        s.add_all([older, latest])
        s.flush()
        ps.recalc(s, owner, START)
        assert run.adjusted_km == latest_value

        ai.set_status(s, owner, latest.id, "reverted", today=START)
        assert run.adjusted_km == older_value
        assert older.status == "approved" and latest.status == "reverted"


def test_ai_history_ui_filters_resets_and_only_offers_eligible_reverts(db, monkeypatch):
    from services import auth
    from utils import bootstrap

    monkeypatch.setattr(bootstrap, "ready", lambda: None)
    monkeypatch.setattr(bootstrap, "get_openai_key", lambda: None)
    monkeypatch.setattr(auth, "runtime_config", lambda: (None, None, None))
    with session_scope() as s:
        owner = ps.ensure_user(s)
        actor = User(name="Friend editor", auth_uid="history-ui-actor")
        s.add(actor)
        s.flush()
        s.add_all([
            _adjustment(
                owner.id, datetime(2026, 10, 20, 9), "applied", kind="friend_edit",
                created_by_user_id=actor.id,
            ),
            _adjustment(owner.id, datetime(2026, 10, 21, 10), "approved"),
            _adjustment(owner.id, datetime(2026, 10, 21, 11), "pending"),
            _adjustment(owner.id, datetime(2026, 10, 21, 12), "rejected"),
            _adjustment(owner.id, datetime(2026, 10, 21, 13), "reverted"),
        ])
        s.add_all(
            _adjustment(owner.id, datetime(2026, 10, 19) + timedelta(minutes=index), "pending")
            for index in range(25)
        )

    app = AppTest.from_file(AI_PAGE).run()
    assert not app.exception
    assert not any("No change history yet." == item.value for item in app.markdown)
    assert sum("History test" in item.value for item in app.caption) == 30
    assert len([button for button in app.button if button.label == "Revert this change"]) == 2
    assert any("Friend Edit" in item.value for item in app.caption)

    app.selectbox(key="ai_history_status").select("Applied").run()
    assert not app.exception
    assert len([button for button in app.button if button.label == "Revert this change"]) == 1
    assert any("Friend Edit" in item.value for item in app.caption)
    assert not any("Pending" in item.value for item in app.markdown)

    app.date_input(key="ai_history_start_date").set_value(date(2026, 10, 22)).run()
    assert not app.exception
    assert any("No changes match these filters." == item.value for item in app.markdown)
    assert not any("No change history yet." == item.value for item in app.markdown)

    app.button(key="ai_history_clear_filters").click().run()
    assert not app.exception
    assert app.selectbox(key="ai_history_status").value == "All"
    assert app.date_input(key="ai_history_start_date").value is None
    assert app.date_input(key="ai_history_end_date").value is None
    assert len([button for button in app.button if button.label == "Revert this change"]) == 2


def test_ai_history_ui_distinguishes_empty_history_from_no_match(db, monkeypatch):
    from services import auth
    from utils import bootstrap

    monkeypatch.setattr(bootstrap, "ready", lambda: None)
    monkeypatch.setattr(bootstrap, "get_openai_key", lambda: None)
    monkeypatch.setattr(auth, "runtime_config", lambda: (None, None, None))

    empty = AppTest.from_file(AI_PAGE).run()
    assert not empty.exception
    assert any(item.value == "No change history yet." for item in empty.markdown)
    assert not any(item.value == "No changes match these filters." for item in empty.markdown)

    with session_scope() as s:
        owner = ps.ensure_user(s)
        s.add(_adjustment(owner.id, datetime(2026, 10, 20), "pending"))
    nonempty = AppTest.from_file(AI_PAGE).run()
    assert not nonempty.exception
    nonempty.selectbox(key="ai_history_status").select("Applied").run()
    assert not nonempty.exception
    assert any(item.value == "No changes match these filters." for item in nonempty.markdown)
    assert not any(item.value == "No change history yet." for item in nonempty.markdown)
