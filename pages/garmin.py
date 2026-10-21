from datetime import date, timedelta, datetime

import streamlit as st
from sqlalchemy import select

import plotly.express as px
import pandas as pd

from database.session import session_scope
from models.orm import RunningSession
from services import garmin_accounts, plan_service as ps
from services.auth import runtime_config
from services.garmin_link import auto_link, link, unlink
from services.garmin_sync import fetch_runs_cached
from utils.bootstrap import get_app_user, ready
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectNotFoundError,
    GarminConnectTooManyRequestsError,
)

ready()
st.title("⌚ Garmin")

def render_sync_status():
    if "garmin_sync_error" in st.session_state:
        st.error(f"Sync failed: {st.session_state['garmin_sync_error']}. Try again or check your Garmin connection in Settings.")
    elif "garmin_sync_last_success" in st.session_state:
        last_sync = st.session_state["garmin_sync_last_success"]
        n = st.session_state["garmin_sync_run_count"]
        if datetime.now() - last_sync < timedelta(hours=24):
            st.success(f"Synced at {last_sync.strftime('%H:%M')} — {n} runs fetched.")
        else:
            st.warning(f"Last synced {int((datetime.now() - last_sync).days)} days ago — data may be out of date.")
    else:
        st.info("No sync yet — press the button above to fetch your Garmin runs.")

render_sync_status()

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
            try:
                with session_scope() as s:
                    user = get_app_user(s)
                    runs = garmin_accounts.fetch_account_runs(s, user)
                    n = auto_link(s, user, runs)
                st.session_state["garmin_runs"] = runs
                st.session_state["garmin_sync_last_success"] = datetime.now()
                st.session_state["garmin_sync_run_count"] = len(runs)
                st.session_state.pop("garmin_sync_error", None)
                st.rerun()
            except (GarminConnectAuthenticationError, GarminConnectTooManyRequestsError,
                    GarminConnectConnectionError, GarminConnectNotFoundError):
                st.session_state["garmin_sync_error"] = "Could not reach Garmin Connect."
                st.rerun()
            except Exception:
                st.session_state["garmin_sync_error"] = "An unexpected error occurred."
                st.rerun()
else:
    if st.button("Sync and auto-match"):
        with st.spinner("Contacting Garmin..."):
            try:
                runs = fetch_runs_cached(st.secrets["GARMIN_EMAIL"], st.secrets["GARMIN_PASSWORD"])
                with session_scope() as s:
                    n = auto_link(s, get_app_user(s), runs)
                st.session_state["garmin_runs"] = runs
                st.session_state["garmin_sync_last_success"] = datetime.now()
                st.session_state["garmin_sync_run_count"] = len(runs)
                st.session_state.pop("garmin_sync_error", None)
                st.rerun()
            except (GarminConnectAuthenticationError, GarminConnectTooManyRequestsError,
                    GarminConnectConnectionError, GarminConnectNotFoundError):
                st.session_state["garmin_sync_error"] = "Could not reach Garmin Connect."
                st.rerun()
            except Exception:
                st.session_state["garmin_sync_error"] = "An unexpected error occurred."
                st.rerun()


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

    matched = [x for x in all_s if x.garmin_id]
    if matched:
        st.subheader("Matched sessions")
        for sess in matched:
            c2 = st.columns([5, 1])
            c2[0].write(f"{sess.date:%a %d %b} — {sess.name or sess.slot}, {sess.actual_km} km (Garmin ID: {sess.garmin_id})")
            if c2[1].button("Unlink", key=f"unlink_{sess.id}"):
                try:
                    unlink(s, user, sess.id)
                    ps.recalc(s, user)
                except ValueError as e:
                    st.error(str(e))
                else:
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