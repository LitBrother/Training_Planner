import streamlit as st
from pydantic import ValidationError
from typing import Literal, cast
from datetime import date

from database.session import session_scope
from models.schemas import PlanSettings
from services import export_service as ex
from services import plan_service as ps
from services.garmin_sync import fetch_runs, garmin_credentials
from services import coaching
from services.ai_service import upcoming_sessions
from utils.bootstrap import ready

ready()
st.title("⚙️ Settings")
rerun = False

with session_scope() as s:
    user = ps.ensure_user(s)
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
    c[0].download_button("Excel export", ex.to_excel(s), "marathon_planner.xlsx")
    table = c[1].selectbox("CSV table", list(ex.TABLES))
    c[2].download_button("CSV export", ex.table_csv(s, table), f"{table}.csv", "text/csv")
    st.download_button("JSON backup", ex.to_json(s), "marathon_backup.json", "application/json")
    up = st.file_uploader("JSON restore", type="json")
    if up and st.checkbox("I understand this replaces all current data") and st.button("Restore backup"):
        try:
            counts = ex.restore_json(s, up.getvalue().decode("utf-8"))
            ps.recalc(s, ps.ensure_user(s))
            st.success(f"Restored: {counts}")
        except Exception as exc:
            st.error(f"Restore failed: {exc}")
st.caption("Streamlit Community Cloud storage is ephemeral. Download a JSON backup regularly.")

st.subheader("Garmin sync")
if st.button("Sync last 30 days from Garmin"):
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
        up = upcoming_sessions(s2, ps.ensure_user(s2), date.today())
    for d, sess in sorted(up.items())[:7]:
        st.markdown(f"**{d:%a %d %b}, {sess.get('type', 'run')}:** {coaching.run_note(sess.get('type', ''), info)}")