from datetime import date

import streamlit as st

from database.session import session_scope
from services import plan_service as ps
from utils.bootstrap import ready

ready()
st.title("🏁 Events")
st.caption("A = goal race | B = important | C = training event. Event weeks keep the same workout order: "
           "Tuesday reduced, Friday shakeout (or reduced), weekend event.")
rerun = False

etype = st.selectbox("Event type", list(ps.EVENT_TYPES))
with st.form("add_event"):
    name = st.text_input("Name")
    d = st.date_input("Date", date.today())
    dist = st.number_input("Distance (km)", 0.0, 100.0, ps.EVENT_TYPES[etype] or 10.0, 0.1, key=f"dist_{etype}")
    prio = st.selectbox("Priority", ["A", "B", "C"], index=2)
    notes = st.text_area("Notes")
    if st.form_submit_button("Add event"):
        if not name.strip() or dist <= 0:
            st.error("Name and a distance above 0 are required.")
        else:
            with session_scope() as s:
                ps.add_event(s, ps.ensure_user(s), name.strip(), etype, d, dist, prio, notes)
            rerun = True

with session_scope() as s:
    user = ps.ensure_user(s)
    for e in ps.list_events(s, user):
        c = st.columns([2, 2, 1, 1, 3, 1])
        c[0].write(f"**{e.name}**")
        c[1].write(f"{e.date:%a %d %b %Y}")
        c[2].write(f"{e.distance_km:g} km")
        c[3].write(f"Priority {e.priority}")
        c[4].write(e.notes or "")
        if e.name != ps.GOAL_NAME and c[5].button("Delete", key=f"del{e.id}"):
            ps.delete_event(s, user, e.id)
            rerun = True
if rerun:
    st.rerun()