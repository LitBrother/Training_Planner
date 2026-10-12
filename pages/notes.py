from datetime import date

import streamlit as st

from database.session import session_scope
from services import plan_service as ps
from utils.bootstrap import ready

ready()
st.title("📝 Notes")
st.caption("Training, injury, travel, fatigue and sleep notes are included in the AI review. "
           "For pain or injury, consult a qualified health professional.")
rerun = False
with st.form("note"):
    d = st.date_input("Date", date.today())
    cat = st.selectbox("Category", ["Training", "Injury", "Travel", "Fatigue", "Sleep", "General"])
    text = st.text_area("Note")
    if st.form_submit_button("Add note") and text.strip():
        with session_scope() as s:
            ps.add_note(s, ps.ensure_user(s), d, cat, text.strip())
        rerun = True
with session_scope() as s:
    for n in ps.list_notes(s, ps.ensure_user(s)):
        c = st.columns([1, 1, 6, 1])
        c[0].write(f"{n.date:%d %b %Y}")
        c[1].write(n.category)
        c[2].write(n.text)
        if c[3].button("Delete", key=f"dn{n.id}"):
            ps.delete_note(s, n.id)
            rerun = True
if rerun:
    st.rerun()