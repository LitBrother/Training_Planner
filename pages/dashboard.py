from datetime import date

import streamlit as st

from database.session import session_scope
from services import plan_service as ps
from services.plan_generator import PHASE_GOALS
from utils import charts
from utils.bootstrap import get_app_user, ready

ready()
st.title("🏃 Training Planner")

with session_scope() as s:
    user = get_app_user(s)
    info = ps.dashboard_summary(s, user, date.today())
    df = ps.weekly_dataframe(s, user)

if info is None:
    st.warning("No training plan found. Open Settings and save to generate one.")
    st.stop()

if info["race_date"]:
    st.caption(f"Welcome, {user.name}. Goal event: {info['race_date']:%A %d %B %Y}.")
else:
    st.caption(f"Welcome, {user.name}. Your training goal is flexible; no event is required.")

d = info["days_to_race"]
c = st.columns(4)
c[0].metric("Race countdown", f"{d} days" if d is not None else "No race set",
            f"{d // 7} weeks" if d is not None else None)
c[1].metric("Current week", f"{info['week_no']} / {info['total_weeks']}")
c[2].metric("Current phase", info["phase"])
c[3].metric("Training status", info["status"])
c = st.columns(4)
c[0].metric("Planned km", f"{info['planned']:g}")
c[1].metric("Adjusted km", f"{info['adjusted']:g}")
c[2].metric("Actual km", f"{info['actual']:g}")
c[3].metric("Completion", f"{info['completion']:.0f}%")
st.progress(min(1.0, info["completion"] / 100))
st.info(f"**{info['phase']}**: {PHASE_GOALS[info['phase']]}")

c1, c2 = st.columns(2)
nl = info["next_long"]
c1.subheader("Next long run")
c1.write(f"{nl.date:%A %d %b}: **{nl.adjusted_km:g} km** ({nl.name})" if nl else "None scheduled")
ne = info["next_event"]
c2.subheader("Next event")
c2.write(f"{ne.date:%A %d %b %Y}: **{ne.name}** ({ne.distance_km:g} km, priority {ne.priority})"
         if ne else "None scheduled")

st.plotly_chart(charts.weekly_volume_fig(df, st.session_state.get("garmin_runs")), use_container_width=True)
st.plotly_chart(charts.long_run_chart(df), use_container_width=True)

if st.session_state.get("garmin_msg"):
    st.caption(f"⌚ {st.session_state['garmin_msg']}")