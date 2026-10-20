from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from database.session import session_scope
from models.orm import StrengthLog
from services import plan_service as ps
from services import strength_progress as sp


STRENGTH_PAGE = Path(__file__).resolve().parent.parent / "pages" / "strength.py"
LOG_DATE = date(2026, 10, 15)


def _render_strength_page(monkeypatch):
    from services import auth
    from utils import bootstrap
    import streamlit as st

    monkeypatch.setattr(bootstrap, "ready", lambda: None)
    monkeypatch.setattr(auth, "runtime_config", lambda: (None, None, None))
    monkeypatch.setattr(st, "page_link", lambda *args, **kwargs: None)
    return AppTest.from_file(STRENGTH_PAGE).run()


def test_strength_page_explains_and_links_to_scheduled_sessions(monkeypatch):
    from services import auth
    from utils import bootstrap
    import streamlit as st

    links = []
    monkeypatch.setattr(bootstrap, "ready", lambda: None)
    monkeypatch.setattr(auth, "runtime_config", lambda: (None, None, None))
    monkeypatch.setattr(st, "page_link", lambda page, **kwargs: links.append((page, kwargs)))

    app = AppTest.from_file(STRENGTH_PAGE).run()

    assert not app.exception
    assert any(
        "individual exercise-level lift logs" in caption.value
        and "scheduled strength sessions are shown separately" in caption.value
        for caption in app.caption
    )
    assert (
        "pages/training_plan.py",
        {
            "label": "View scheduled strength sessions in the Weekly Planner",
            "icon": "🗓️",
        },
    ) in links


def test_strength_log_edits_and_deletes_are_owner_scoped(db):
    with session_scope() as s:
        owner = ps.ensure_user(s, auth_uid="strength-owner")
        other = ps.ensure_user(s, auth_uid="strength-other")
        owned = StrengthLog(
            user_id=owner.id, date=LOG_DATE, exercise="Bench Press",
            weight_kg=50, sets=3, reps=8,
        )
        other_log = StrengthLog(
            user_id=other.id, date=LOG_DATE, exercise="Squat",
            weight_kg=80, sets=4, reps=6,
        )
        s.add_all([owned, other_log])
        s.flush()
        original_id, original_user_id = owned.id, owned.user_id
        other_id, other_user_id = other_log.id, other_log.user_id

        with pytest.raises(ValueError) as missing:
            sp.update_log(s, owner, 999999, LOG_DATE, "Lift", 10, 2, 5)
        with pytest.raises(ValueError) as not_owned:
            sp.update_log(s, other, owned.id, LOG_DATE, "Lift", 10, 2, 5)
        assert type(missing.value) is type(not_owned.value)
        assert str(missing.value) == str(not_owned.value) == "Strength log not found for this account."

        with pytest.raises(ValueError) as missing_delete:
            sp.delete_log(s, owner, 999999)
        with pytest.raises(ValueError) as unauthorized_delete:
            sp.delete_log(s, other, owned.id)
        assert type(missing_delete.value) is type(unauthorized_delete.value)
        assert str(missing_delete.value) == str(unauthorized_delete.value) == str(missing.value)

        with pytest.raises(ValueError, match="not found for this account"):
            sp.update_log(s, owner, other_log.id, LOG_DATE, "Changed", 10, 2, 5)
        with pytest.raises(ValueError, match="not found for this account"):
            sp.delete_log(s, owner, other_log.id)
        assert (other_log.id, other_log.user_id, other_log.date, other_log.exercise,
                other_log.weight_kg, other_log.sets, other_log.reps) == (
                    other_id, other_user_id, LOG_DATE, "Squat", 80, 4, 6
                )

        sp.update_log(
            s, owner, owned.id, date(2026, 10, 19), "  front squat ", 72.5, 5, 10
        )
        assert (owned.id, owned.user_id) == (original_id, original_user_id)
        assert (owned.date, owned.exercise, owned.weight_kg, owned.sets, owned.reps) == (
            date(2026, 10, 19), "Front Squat", 72.5, 5, 10
        )
        sp.delete_log(s, owner, owned.id)
        assert s.get(StrengthLog, owned.id) is None
        assert (other_log.id, other_log.user_id, other_log.exercise) == (
            other_id, other_user_id, "Squat"
        )


def test_strength_log_validation_accepts_inclusive_field_bounds(db):
    with session_scope() as s:
        user = ps.ensure_user(s)
        sp.add_log(s, user, LOG_DATE, "  zero load ", 0, 1, 1)
        row = sp.list_logs(s, user)[0]
        assert (row.exercise, row.weight_kg, row.sets, row.reps) == ("Zero Load", 0, 1, 1)

        sp.update_log(s, user, row.id, LOG_DATE, "Maximum Lift", 500, 10, 50)
        assert (row.exercise, row.weight_kg, row.sets, row.reps) == (
            "Maximum Lift", 500, 10, 50
        )


def test_invalid_strength_log_date_is_rejected_without_mutation(db):
    with session_scope() as s:
        user = ps.ensure_user(s)
        row = StrengthLog(
            user_id=user.id, date=LOG_DATE, exercise="Bench Press",
            weight_kg=50, sets=3, reps=8,
        )
        s.add(row)
        s.flush()
        original = (row.date, row.exercise, row.weight_kg, row.sets, row.reps)

        with pytest.raises(ValueError, match="Date must be a valid date"):
            sp.add_log(s, user, "not-a-date", "Squat", 80, 4, 6)
        assert (row.date, row.exercise, row.weight_kg, row.sets, row.reps) == original
        assert len(sp.list_logs(s, user)) == 1

        with pytest.raises(ValueError, match="Date must be a valid date"):
            sp.update_log(s, user, row.id, "not-a-date", "Changed Lift", 60, 4, 8)
        assert (row.date, row.exercise, row.weight_kg, row.sets, row.reps) == original
        assert len(sp.list_logs(s, user)) == 1


@pytest.mark.parametrize(
    ("exercise", "weight", "sets", "reps", "message"),
    [
        (" \t ", 10, 3, 8, "Exercise"),
        ("Bench", -0.01, 3, 8, "Weight"),
        ("Bench", 500.01, 3, 8, "Weight"),
        ("Bench", float("nan"), 3, 8, "Weight"),
        ("Bench", 10, 0, 8, "Sets"),
        ("Bench", 10, 11, 8, "Sets"),
        ("Bench", 10, 3.5, 8, "Sets"),
        ("Bench", 10, 3, 0, "Reps"),
        ("Bench", 10, 3, 51, "Reps"),
    ],
)
def test_invalid_strength_log_values_are_rejected_without_mutation(
    db, exercise, weight, sets, reps, message
):
    with session_scope() as s:
        user = ps.ensure_user(s)
        row = StrengthLog(
            user_id=user.id, date=LOG_DATE, exercise="Bench Press",
            weight_kg=50, sets=3, reps=8,
        )
        s.add(row)
        s.flush()
        original = (row.date, row.exercise, row.weight_kg, row.sets, row.reps)

        with pytest.raises(ValueError, match=message):
            sp.update_log(s, user, row.id, date(2026, 10, 20), exercise, weight, sets, reps)
        assert (row.date, row.exercise, row.weight_kg, row.sets, row.reps) == original

        with pytest.raises(ValueError, match=message):
            sp.add_log(s, user, LOG_DATE, exercise, weight, sets, reps)
        assert len(sp.list_logs(s, user)) == 1


def test_strength_edit_cancel_and_delete_confirmation_do_not_mutate(db, monkeypatch):
    with session_scope() as s:
        user = ps.ensure_user(s)
        row = StrengthLog(
            user_id=user.id, date=LOG_DATE, exercise="Bench Press",
            weight_kg=50, sets=3, reps=8,
        )
        s.add(row)
        s.flush()
        log_id = row.id

    app = _render_strength_page(monkeypatch)
    assert not app.exception
    next(button for button in app.button if button.label == "Edit").click().run()
    app.text_input(key=f"edit_strength_exercise_{log_id}_1").set_value("Changed Lift")
    app.number_input(key=f"edit_strength_weight_{log_id}_1").set_value(90)
    app.date_input(key=f"edit_strength_date_{log_id}_1").set_value(date(2026, 10, 20))
    next(button for button in app.button if button.label == "Cancel").click().run()
    assert not app.exception
    with session_scope() as s:
        row = s.get(StrengthLog, log_id)
        assert (row.date, row.exercise, row.weight_kg, row.sets, row.reps) == (
            LOG_DATE, "Bench Press", 50, 3, 8
        )

    next(button for button in app.button if button.label == "Delete").click().run()
    assert "Permanently delete Bench Press from 15 Oct 2026?" in [item.value for item in app.warning]
    next(button for button in app.button if button.label == "Cancel deletion").click().run()
    assert not app.exception
    with session_scope() as s:
        assert s.get(StrengthLog, log_id) is not None


def test_strength_edit_refreshes_progression_and_delete_shows_empty_state(db, monkeypatch):
    with session_scope() as s:
        user = ps.ensure_user(s)
        row = StrengthLog(
            user_id=user.id, date=LOG_DATE, exercise="Bench Press",
            weight_kg=50, sets=3, reps=8,
        )
        s.add(row)
        s.flush()
        log_id = row.id

    app = _render_strength_page(monkeypatch)
    assert not app.exception
    next(button for button in app.button if button.label == "Edit").click().run()
    app.text_input(key=f"edit_strength_exercise_{log_id}_1").set_value("Front Squat")
    app.number_input(key=f"edit_strength_weight_{log_id}_1").set_value(70)
    app.number_input(key=f"edit_strength_reps_{log_id}_1").set_value(10)
    app.date_input(key=f"edit_strength_date_{log_id}_1").set_value(date(2026, 10, 19))
    next(button for button in app.button if button.label == "Save changes").click().run()
    assert not app.exception
    assert "Add load: 72.5 kg x 5 reps per set." in [item.value for item in app.success]
    assert any("19 Oct 2026" in item.value for item in app.markdown)
    with session_scope() as s:
        row = s.get(StrengthLog, log_id)
        assert (row.date, row.exercise, row.weight_kg, row.sets, row.reps) == (
            date(2026, 10, 19), "Front Squat", 70, 3, 10
        )
        frame = sp.log_frame(s, user)
        assert sp.suggest_next(frame, "Front Squat") == "Add load: 72.5 kg x 5 reps per set."

    next(button for button in app.button if button.label == "Delete").click().run()
    next(button for button in app.button if button.label == "Confirm delete").click().run()
    assert not app.exception
    assert any("No strength logs yet" in item.value for item in app.info)
    assert not app.selectbox
    with session_scope() as s:
        assert s.get(StrengthLog, log_id) is None
        assert sp.log_frame(s, user).empty
