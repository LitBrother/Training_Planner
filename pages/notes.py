from datetime import date

import streamlit as st

from database.session import session_scope
from services import plan_service as ps
from utils.bootstrap import get_app_user, ready

ready()
st.title("📝 Notes")
st.caption("Recent notes (date, category, and text) are excluded from AI reviews by default. You can explicitly opt "
           "in for an individual review on the AI Coach page; if you do, recent note text and its date/category are "
           "sent to Google Gemini for that review. For pain or injury, consult a qualified health professional.")
rerun = False
with st.form("note"):
    d = st.date_input("Date", date.today())
    cat = st.selectbox("Category", ps.NOTE_CATEGORIES)
    text = st.text_area("Note")
    if st.form_submit_button("Add note") and text.strip():
        with session_scope() as s:
            ps.add_note(s, get_app_user(s), d, cat, text.strip())
        rerun = True

clear_col, category_clear_col, reset_col = st.columns(3)
if clear_col.button("Clear dates"):
    st.session_state["notes_start_date"] = None
    st.session_state["notes_end_date"] = None
if category_clear_col.button("Clear categories"):
    st.session_state["notes_category"] = "All"
if reset_col.button("Reset all"):
    st.session_state["notes_start_date"] = None
    st.session_state["notes_end_date"] = None
    st.session_state["notes_category"] = "All"

filter_cols = st.columns(3)
start_date = filter_cols[0].date_input("Start date", value=None, key="notes_start_date")
end_date = filter_cols[1].date_input("End date", value=None, key="notes_end_date")
category = filter_cols[2].selectbox(
    "Category", ("All", *ps.NOTE_CATEGORIES), key="notes_category"
)

with session_scope() as s:
    user = get_app_user(s)
    notes = ps.list_notes(s, user)
    unknown_categories = sorted({n.category for n in notes if n.category not in ps.NOTE_CATEGORIES})
    if unknown_categories:
        with st.expander("Historical categories outside the current list"):
            st.write(
                "These older categories remain available when Category is All: "
                + ", ".join(unknown_categories)
            )

    if start_date is not None and end_date is not None and start_date > end_date:
        st.error("Start date must be on or before end date.")
        filtered_notes = []
    else:
        filtered_notes = [
            n for n in notes
            if (start_date is None or n.date >= start_date)
            and (end_date is None or n.date <= end_date)
            and (category == "All" or n.category == category)
        ]
        count = len(filtered_notes)
        st.caption(f"{count} {'note' if count == 1 else 'notes'}")

    if not filtered_notes and not (
        start_date is not None and end_date is not None and start_date > end_date
    ):
        st.info("No notes match these filters. Try changing or clearing a date or category filter.")

    for n in filtered_notes:
        c = st.columns([1, 1, 6, 1])
        c[0].write(f"{n.date:%d %b %Y}")
        c[1].write(n.category)
        c[2].write(n.text)
        with c[3]:
            if st.button("Edit", key=f"en{n.id}"):
                st.session_state[f"edit_note_{n.id}"] = not st.session_state.get(f"edit_note_{n.id}", False)
        if st.session_state.get(f"edit_note_{n.id}"):
            with st.form(f"edit_note_form_{n.id}"):
                edit_date = st.date_input("Date", n.date, key=f"edit_note_date_{n.id}")
                edit_category = st.selectbox(
                    "Category", ps.NOTE_CATEGORIES, index=ps.NOTE_CATEGORIES.index(n.category)
                    if n.category in ps.NOTE_CATEGORIES else 0, key=f"edit_note_category_{n.id}"
                )
                edit_text = st.text_area("Note", n.text, key=f"edit_note_text_{n.id}")
                save_col, cancel_col = st.columns(2)
                save = save_col.form_submit_button("Save changes")
                cancel = cancel_col.form_submit_button("Cancel")
            if save:
                try:
                    with session_scope() as s:
                        ps.update_note(s, get_app_user(s), n.id, edit_date, edit_category, edit_text)
                    st.session_state.pop(f"edit_note_{n.id}", None)
                    rerun = True
                except ValueError as exc:
                    st.error(str(exc))
            elif cancel:
                st.session_state.pop(f"edit_note_{n.id}", None)
                rerun = True
        if c[3].button("Delete", key=f"dn{n.id}"):
            ps.delete_note(s, n.id, user)
            rerun = True
if rerun:
    st.rerun()