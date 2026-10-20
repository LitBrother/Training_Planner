import streamlit as st
from pydantic import ValidationError
from typing import Literal, cast
from datetime import date

from database.session import session_scope
from models.schemas import PlanSettings
from services import export_service as ex
from services import plan_service as ps
from services.garmin_sync import fetch_runs, garmin_credentials
from services import garmin_accounts
from services.garmin_link import auto_link
from services.auth import runtime_config
from services import coaching
from services.ai_service import upcoming_sessions
from utils.bootstrap import get_app_user, ready

ready()
st.title("⚙️ Settings")
rerun = False

with session_scope() as s:
    user = get_app_user(s)
    cfg = ps.get_settings(s, user)
    with st.form("settings"):
        name = st.text_input("Name", cfg.user_name)
        has_goal = st.checkbox("I have a goal race", value=cfg.race_date is not None)
        race = st.date_input("Race date", cfg.race_date or date.today()) if has_goal else None
        horizon = st.number_input("Plan length (weeks)", 4, 104, cfg.horizon_weeks, disabled=has_goal)
        start = st.date_input("Plan start", cfg.plan_start)
        peak = st.number_input("Peak long run (km)", 20.0, 38.0, cfg.peak_long_km, 0.5)
        start_long = st.number_input("Starting long run (km)", 5.0, 30.0, cfg.start_long_km, 0.5)
        taper = st.number_input("Taper length (weeks)", 1, 4, cfg.taper_weeks)
        wk = st.selectbox("Weekend run day", ["Saturday", "Sunday"], index=["Saturday", "Sunday"].index(cfg.weekend_day))
        models = ["gemini-3.5-flash-lite", "gemini-flash-latest", "gemini-2.5-flash"]
        current = cfg.openai_model if cfg.openai_model in models else models[0]
        model = st.selectbox("Gemini model", models, index=models.index(current))
        if st.form_submit_button("Save and regenerate plan"):
            try:
                new = PlanSettings(user_name=name, race_date=race, plan_start=start, peak_long_km=peak,
                                   start_long_km=start_long, taper_weeks=int(taper),
                                   weekend_day=cast(Literal["Saturday", "Sunday"], wk),
                                   openai_model=model)
                ps.save_settings(s, user, new)
                ps.sync_plan(s, user, new)
                st.success("Saved. Logged sessions were preserved.")
                st.session_state.pop("_synced", None)
            except (ValidationError, ValueError) as exc:
                st.error(str(exc))
    st.text_input("Gemini API key (kept in this browser session only)", type="password", key="openai_key")

    st.subheader("Data")
    c = st.columns(3)
    c[0].download_button("Excel export", ex.to_excel(s, user), "marathon_planner.xlsx")
    table = c[1].selectbox("CSV table", list(ex.TABLES))
    c[2].download_button("CSV export", ex.table_csv(s, table, user), f"{table}.csv", "text/csv")
    st.download_button("JSON backup", ex.to_json(s, user), "marathon_backup.json", "application/json")
    up = st.file_uploader("JSON restore", type="json")
    if up and st.checkbox("I understand this replaces my account's training data") and st.button("Restore backup"):
        try:
            counts = ex.restore_json(s, up.getvalue().decode("utf-8"), user)
            ps.recalc(s, user)
            st.success(f"Restored: {counts}")
        except Exception as exc:
            st.error(f"Restore failed: {exc}")
st.caption("Your hosted database persists across app restarts. Keep a JSON backup as an extra safeguard.")

st.subheader("Garmin sync")
hosted = runtime_config()[0] is not None
if hosted:
    st.caption("Credentials are encrypted in the database using GARMIN_ENCRYPTION_KEY from Streamlit Secrets.")
    with session_scope() as gs:
        g_user = get_app_user(gs)
        try:
            credentials = garmin_accounts.credentials(gs, g_user)
        except RuntimeError as exc:
            credentials = None
            st.warning(str(exc))
    with st.form("garmin_account", clear_on_submit=True):
        garmin_email = st.text_input("Your Garmin email", value=credentials[0] if credentials else "")
        garmin_password = st.text_input("Your Garmin password", type="password")
        save_garmin = st.form_submit_button("Save Garmin connection")
    if save_garmin:
        try:
            with session_scope() as gs:
                garmin_accounts.save_credentials(gs, get_app_user(gs), garmin_email, garmin_password)
            st.success("Garmin credentials encrypted and saved for this account.")
            st.rerun()
        except (ValueError, RuntimeError) as exc:
            st.error(f"Could not save Garmin connection: {exc}")
    if credentials and st.button("Remove saved Garmin connection"):
        with session_scope() as gs:
            garmin_accounts.remove_credentials(gs, get_app_user(gs))
        st.session_state.pop("garmin_runs", None)
        st.success("Garmin connection removed.")
        st.rerun()
    if credentials and st.button("Sync my Garmin runs"):
        try:
            with st.spinner("Contacting Garmin..."):
                with session_scope() as gs:
                    g_user = get_app_user(gs)
                    runs = garmin_accounts.fetch_account_runs(gs, g_user)
                    linked = auto_link(gs, g_user, runs)
                st.session_state["garmin_runs"] = runs
            st.success(f"Synced {len(runs)} runs; linked {linked} to your plan.")
        except Exception as exc:
            st.error(f"Garmin sync failed: {exc}")
elif st.button("Sync last 30 days from Garmin"):
    try:
        with st.spinner("Contacting Garmin..."):
            runs = fetch_runs(*garmin_credentials())
        st.session_state["garmin_runs"] = runs
    except Exception as exc:
        st.error(f"Garmin sync failed: {exc}")
if "garmin_runs" in st.session_state:
    runs = st.session_state["garmin_runs"]
    st.dataframe(runs)
    info = coaching.analyze(runs, date.today())
    c1, c2, c3 = st.columns(3)
    c1.metric("Last 7 days (km)", info["acute_km"])
    c2.metric("4-week avg (km/wk)", info["chronic_weekly_km"])
    c3.metric("Load ratio", info["acwr"] if info["acwr"] is not None else "n/a")
    for f in info["flags"]:
        st.warning(f)
    if not info["flags"]:
        st.success("Training load looks sensible. Stay with the plan.")

    with session_scope() as s2:
        up = upcoming_sessions(s2, get_app_user(s2), date.today())
    for d, sess in sorted(up.items())[:7]:
        st.markdown(f"**{d:%a %d %b}, {sess.get('type', 'run')}:** {coaching.run_note(sess.get('type', ''), info)}")