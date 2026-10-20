from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from database.session import session_scope
from models.orm import Note
from services import plan_service as ps


NOTES_PAGE = Path(__file__).resolve().parent.parent / "pages" / "notes.py"


def test_update_note_is_owner_scoped_and_preserves_creation_fields(db):
    with session_scope() as s:
        owner = ps.ensure_user(s, auth_uid="note-owner")
        other = ps.ensure_user(s, auth_uid="note-other")
        ps.add_note(s, owner, date(2026, 10, 15), "Training", "Original")
        note = ps.list_notes(s, owner)[0]
        original_id, original_user_id, original_created_at = note.id, note.user_id, note.created_at

        ps.update_note(s, owner, note.id, date(2026, 10, 16), "Sleep", "  Updated text  ")
        assert (note.id, note.user_id, note.created_at) == (
            original_id, original_user_id, original_created_at
        )
        assert (note.date, note.category, note.text) == (
            date(2026, 10, 16), "Sleep", "Updated text"
        )

        with pytest.raises(ValueError, match="Note not found for this account"):
            ps.update_note(s, other, note.id, date(2026, 10, 17), "General", "Not theirs")
        with pytest.raises(ValueError, match="Note not found for this account"):
            ps.update_note(s, owner, note.id + 999, date(2026, 10, 17), "General", "Missing")

        with pytest.raises(ValueError, match="category"):
            ps.update_note(s, owner, note.id, date(2026, 10, 18), "Other", "Invalid category")
        with pytest.raises(ValueError, match="blank"):
            ps.update_note(s, owner, note.id, date(2026, 10, 18), "General", " \t\n ")
        assert (note.date, note.category, note.text) == (
            date(2026, 10, 16), "Sleep", "Updated text"
        )


def test_note_edit_cancel_does_not_mutate_and_save_updates(db, monkeypatch):
    from services import auth
    from utils import bootstrap

    monkeypatch.setattr(bootstrap, "ready", lambda: None)
    monkeypatch.setattr(auth, "runtime_config", lambda: (None, None, None))
    with session_scope() as s:
        user = ps.ensure_user(s)
        ps.add_note(s, user, date(2026, 10, 15), "Training", "Original note")
        note_id = ps.list_notes(s, user)[0].id

    app = AppTest.from_file(NOTES_PAGE).run()
    assert not app.exception
    next(button for button in app.button if button.label == "Edit").click().run()
    assert not app.exception

    next(button for button in app.button if button.label == "Cancel").click().run()
    with session_scope() as s:
        note = s.get(Note, note_id)
        assert (note.date, note.category, note.text) == (
            date(2026, 10, 15), "Training", "Original note"
        )

    next(button for button in app.button if button.label == "Edit").click().run()
    app.text_area(key=f"edit_note_text_{note_id}").set_value("Edited note")
    app.selectbox(key=f"edit_note_category_{note_id}").select("Fatigue")
    app.date_input(key=f"edit_note_date_{note_id}").set_value(date(2026, 10, 19))
    next(button for button in app.button if button.label == "Save changes").click().run()
    assert not app.exception
    with session_scope() as s:
        note = s.get(Note, note_id)
        assert (note.date, note.category, note.text) == (
            date(2026, 10, 19), "Fatigue", "Edited note"
        )


def test_note_filters_are_inclusive_category_scoped_and_read_only(db, monkeypatch):
    from services import auth
    from utils import bootstrap

    monkeypatch.setattr(bootstrap, "ready", lambda: None)
    monkeypatch.setattr(auth, "runtime_config", lambda: (None, None, None))
    with session_scope() as s:
        other = ps.ensure_user(s, auth_uid="notes-filter-other")
        owner = ps.ensure_user(s)
        ps.add_note(s, owner, date(2026, 10, 14), "Training", "Before range")
        ps.add_note(s, owner, date(2026, 10, 15), "Training", "Start boundary")
        ps.add_note(s, owner, date(2026, 10, 16), "Sleep", "End boundary")
        ps.add_note(s, owner, date(2026, 10, 17), "Historical", "Legacy category")
        ps.add_note(s, other, date(2026, 10, 15), "Training", "Other account")

    app = AppTest.from_file(NOTES_PAGE).run()
    assert not app.exception
    assert "4 notes" in [item.value for item in app.caption]
    assert "Historical categories outside the current list" in [item.label for item in app.expander]
    assert any("Historical" in item.value for item in app.markdown)
    assert [item.value for item in app.markdown if item.value in {
        "Before range", "Start boundary", "End boundary", "Legacy category", "Other account"
    }] == ["Legacy category", "End boundary", "Start boundary", "Before range"]

    app.date_input(key="notes_start_date").set_value(date(2026, 10, 15))
    app.date_input(key="notes_end_date").set_value(date(2026, 10, 16))
    app.run()
    assert not app.exception
    assert "2 notes" in [item.value for item in app.caption]
    assert [item.value for item in app.markdown if item.value in {
        "Start boundary", "End boundary", "Before range", "Legacy category"
    }] == ["End boundary", "Start boundary"]

    app.selectbox(key="notes_category").select("Sleep").run()
    assert not app.exception
    assert "1 note" in [item.value for item in app.caption]
    assert [item.value for item in app.markdown if item.value in {
        "Start boundary", "End boundary"
    }] == ["End boundary"]

    with session_scope() as s:
        assert [n.text for n in ps.list_notes(s, owner)] == [
            "Legacy category", "End boundary", "Start boundary", "Before range"
        ]
        assert [n.text for n in ps.list_notes(s, other)] == ["Other account"]


def test_note_filter_clear_actions_and_invalid_or_empty_results(db, monkeypatch):
    from services import auth
    from utils import bootstrap

    monkeypatch.setattr(bootstrap, "ready", lambda: None)
    monkeypatch.setattr(auth, "runtime_config", lambda: (None, None, None))
    with session_scope() as s:
        user = ps.ensure_user(s)
        ps.add_note(s, user, date(2026, 10, 15), "Training", "Only note")

    app = AppTest.from_file(NOTES_PAGE).run()
    app.date_input(key="notes_start_date").set_value(date(2026, 10, 20))
    app.date_input(key="notes_end_date").set_value(date(2026, 10, 10))
    app.run()
    assert not app.exception
    assert [item.value for item in app.error] == ["Start date must be on or before end date."]
    assert not any(item.value.endswith((" note", " notes")) for item in app.caption)
    assert "Only note" not in [item.value for item in app.markdown]

    next(button for button in app.button if button.label == "Reset all").click().run()
    assert not app.exception
    app.selectbox(key="notes_category").select("Sleep").run()
    assert "0 notes" in [item.value for item in app.caption]
    assert any("No notes match these filters" in item.value for item in app.info)

    app.date_input(key="notes_start_date").set_value(date(2026, 10, 10))
    app.run()
    next(button for button in app.button if button.label == "Clear categories").click().run()
    assert app.selectbox(key="notes_category").value == "All"
    assert app.date_input(key="notes_start_date").value == date(2026, 10, 10)
    assert "1 note" in [item.value for item in app.caption]

    app.selectbox(key="notes_category").select("Training").run()
    next(button for button in app.button if button.label == "Clear dates").click().run()
    assert app.selectbox(key="notes_category").value == "Training"
    assert app.date_input(key="notes_start_date").value is None
    assert app.date_input(key="notes_end_date").value is None
    assert "1 note" in [item.value for item in app.caption]
