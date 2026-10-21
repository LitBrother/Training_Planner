from datetime import date, timedelta

import streamlit as st
from sqlalchemy import select

import plotly.express as px
import pandas as pd

from database.session import session_scope
from models.orm import RunningSession
from services import garmin_accounts, plan_service as ps
from services.auth import runtime_config
from services.garmin_link import auto_link, link
from services.garmin_sync import fetch_runs_cached
from utils.bootstrap import get_app_user, ready

ready()
st.title("⌚ Garmin")

is_hosted = runtime_config()[0] is not None

if is_hosted:
    with session_scope() as s:
        user = get_app_user(s)
        creds = garmin_accounts.credentials(s, user)
        if creds is None:
            st.info("Connect your Garmin account in **Settings → Garmin** to sync your runs.")
            st.stop()
    if st.button("Sync my Garmin runs"):
        with st.spinner("Contacting Garmin..."):
            with session_scope() as s:
                user = get_app_user(s)
                st.session_state["garmin_runs"] = garmin_accounts.fetch_account_runs(s, user)
                n = auto_link(s, user, st.session_state["garmin_runs"])
        st.success(f"Matched {n} runs to sessions.")
else:
    if st.button("Sync and auto-match"):
        with st.spinner("Contacting Garmin..."):
            st.session_state["garmin_runs"] = fetch_runs_cached(st.secrets["GARMIN_EMAIL"], st.secrets["GARMIN_PASSWORD"])
        with session_scope() as s:
            n = auto_link(s, get_app_user(s), st.session_state["garmin_runs"])
        st.success(f"Matched {n} runs to sessions.")

runs = st.session_state.get("garmin_runs", [])
with session_scope() as s:
    user = get_app_user(s)
    all_s = list(s.scalars(select(RunningSession).where(RunningSession.user_id == user.id)))
    linked = {x.garmin_id for x in all_s if x.garmin_id}
    st.subheader("Unmatched runs")
    for r in [r for r in runs if r["id"] not in linked]:
        near = [x for x in all_s if abs((x.date - r["date"]).days) <= 7 and not x.garmin_id]
        c = st.columns([3, 4, 1])
        c[0].write(f"{r['date']:%a %d %b}, {r['distance_km']} km")
        pick = c[1].selectbox("Session", near, format_func=lambda x: f"{x.date:%a %d %b}, {x.adjusted_km} km",
                              key=f"p{r['id']}", label_visibility="collapsed")
        if c[2].button("Link", key=f"b{r['id']}") and pick:
            link(s, user, pick.id, r)
            ps.recalc(s, user)
            st.rerun()

if runs:
    df = pd.DataFrame(runs)
    df["pace"] = df["duration_min"] / df["distance_km"]
    df["week"] = pd.to_datetime(df["date"]).dt.to_period("W").dt.start_time
    wk = df.groupby("week", as_index=False)["distance_km"].sum()
    st.plotly_chart(px.bar(wk, x="week", y="distance_km", title="Weekly km (Garmin)",
                           template="plotly_dark", color_discrete_sequence=["#00D4AA"]), use_container_width=True)
    st.plotly_chart(px.scatter(df, x="date", y="pace", size="distance_km", title="Pace (min/km)",
                               template="plotly_dark"), use_container_width=True)
    if df["avg_hr"].notna().any():
        st.plotly_chart(px.line(df.dropna(subset=["avg_hr"]), x="date", y="avg_hr", markers=True,
                                title="Average HR", template="plotly_dark"), use_container_width=True)