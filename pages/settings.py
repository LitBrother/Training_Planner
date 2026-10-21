import streamlit as st
from pydantic import ValidationError
from typing import Literal, cast
from datetime import date

from database.session import session_scope
from models.schemas import PlanSettings
from services import export_service as ex, reliability
from services import plan_service as ps
from services.athlete_profile import get_profile
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
        st.subheader("Plan")
        name = st.text_input("Name", cfg.user_name)
        focus = st.selectbox("Training focus", ["Running", "Strength", "Both"],
                             index=["Running", "Strength", "Both"].index(cfg.training_focus))
        # ... inside the settings form ...
        goal = st.text_input("Training goal", cfg.goal)
        
        # TP-32: Wellness data sharing consent
        profile = get_profile(s, user)
        share_wellness = st.checkbox("Share Garmin recovery data with AI", value=profile.share_garmin_recovery_with_ai)
        if not share_wellness and profile.share_garmin_recovery_with_ai:
            st.warning("Wellness sharing revoked. Existing Garmin wellness caches will be cleared.")
        
        has_goal = st.checkbox("I have a goal event", value=cfg.race_date is not None)
        race = st.date_input("Goal event date", cfg.race_date or date.today()) if has_goal else None
        race_distance = st.number_input("Goal event distance (km)", 1.0, 100.0, cfg.race_distance_km, 0.1,
                                        disabled=not has_goal)
        horizon = st.number_input("Plan length (weeks)", 4, 104, cfg.horizon_weeks, disabled=has_goal)
        start = st.date_input("Plan start", cfg.plan_start)
        peak = st.number_input("Peak long run (km)", 8.0, 38.0, cfg.peak_long_km, 0.5)
        start_long = st.number_input("Starting long run (km)", 5.0, 30.0, cfg.start_long_km, 0.5)
        taper = st.number_input("Taper length (weeks)", 1, 4, cfg.taper_weeks)
        wk = st.selectbox("Weekend run day", ["Saturday", "Sunday"], index=["Saturday", "Sunday"].index(cfg.weekend_day))
        st.subheader("AI")
        models = ["gemini-3.5-flash-lite", "gemini-flash-latest", "gemini-2.5-flash"]
        current = cfg.openai_model if cfg.openai_model in models else models[0]
        model = st.selectbox("Gemini model", models, index=models.index(current))
        if st.form_submit_button("Save and regenerate plan"):
            try:
                new = PlanSettings(user_name=name, race_date=race, race_distance_km=race_distance,
                                   training_focus=focus, goal=goal, plan_start=start, peak_long_km=peak,
                                   start_long_km=start_long, taper_weeks=int(taper),
                                   weekend_day=cast(Literal["Saturday", "Sunday"], wk),
                                   openai_model=model, horizon_weeks=int(horizon))
                ps.save_settings(s, user, new)
                ps.sync_plan(s, user, new)
                profile = get_profile(s, user)
                profile.training_focus, profile.goal = focus, goal.strip()
                profile.share_garmin_recovery_with_ai = share_wellness
                if not share_wellness:
                    st.session_state.pop("garmin_wellness", None)
                    st.session_state.pop("_ai_plan_preview", None) # Fix Finding 4
                st.success("Saved. Logged sessions were preserved.")
                st.session_state.pop("_synced", None)
            except (ValidationError, ValueError) as exc:
                st.error(str(exc))
    st.text_input("Gemini API key (kept in this browser session only)", type="password", key="openai_key")

    st.subheader("Data")
# TP-46: Restore preview before replacing account data
backup_file = st.file_uploader("JSON restore", type="json")
if backup_file:
    try:
        preview = restore_preview.preview_restore(s, user, backup_file.getvalue().decode("utf-8"))
        st.subheader("Restore Preview")
        st.markdown(f"""This backup contains:

- {preview['users']} user record (you)
- {preview['athlete_profile']} profile entry
- {preview['training_weeks']} training weeks
- {preview['running_sessions']} running sessions
- {preview['strength_sessions']} strength sessions
- {preview['strength_logs']} strength logs
- {preview['strength_set_details']} per-set details
- {preview['notes']} notes
- {preview['events']} events
- {preview['ai_adjustments']} AI adjustments
- {preview['settings']} settings
- {preview['run_correction_history']} run corrections

The restore will replace only your current account's data. Garmin credentials and other accounts' data remain untouched.

Click below to confirm and restore."""")
        if st.button("Confirm Restore"):
            try:
                counts = restore_preview.restore_with_preview(s, user, backup_file.getvalue().decode("utf-8"))
                st.success(f"Restored {counts}")
            except Exception as exc:
                st.error(f"Restore failed: {exc}")
    except Exception as exc:
        st.error(f"Invalid backup file: {exc}")
    table = st.selectbox("CSV table", list(ex.TABLES))
    st.markdown(ex.manifest_text("csv", user, table))
    st.markdown(ex.manifest_text("excel", user))
    st.markdown(ex.manifest_text("json", user))
    c = st.columns(2)
    c[0].download_button("Excel export", ex.to_excel(s, user), "training_planner.xlsx")
    c[1].download_button(
        "CSV export", ex.table_csv(s, table, user), f"{table}.csv", "text/csv"
    )
    st.download_button(
        "JSON backup", ex.to_json(s, user), "training_planner_backup.json", "application/json"
    )
    up = st.file_uploader("JSON restore", type="json")
    if up and st.checkbox(
        "I understand this replaces only my current account's exported data; saved Garmin credentials remain preserved"
    ) and st.button("Restore backup"):
        try:
            counts = ex.restore_json(s, up.getvalue().decode("utf-8"), user)
            ps.recalc(s, user)
            st.success(f"Restored: {counts}")
        except Exception as exc:
            st.error(f"Restore failed: {exc}")
st.caption("Your hosted database persists across app restarts. Keep a JSON backup as an extra safeguard.")
# TP-47: Operational logging without exposing sensitive info
st.subheader("Operational logs")
if st.button("View recent logs"):
    try:
        with session_scope() as s:
            user = get_app_user(s)
            events = reliability.recent_operational_events(s, user)
            st.markdown("Recent operational events (last 7 days):")
            for e in events:
                details = reliability.redact_sensitive_details(e.details)
                st.markdown(f"- {e.event_type}: {details or 'No details'} ({e.timestamp:%Y-%m-%d %H:%M})" if details else f"- {e.event_type} ({e.timestamp:%Y-%m-%d %H:%M})")
    except Exception as exc:
        st.error(f"Could not load logs: {exc}")
# TP-48: Weekly reflection optional
st.subheader("Weekly reflection")
reflect = st.checkbox("Show weekly reflection prompt", value=False)
if reflect:
    st.markdown("Optional: Reflect on your week. This does not affect your plan or show streaks.")

st.subheader("Garmin")
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
    # TP-30: Robust disconnect state handling
    if credentials:
        if "confirm_disconnect" not in st.session_state:
            if st.button("Remove saved Garmin connection"):
                st.session_state["confirm_disconnect"] = True
                st.rerun()
        else:
            retain_data = st.checkbox("Retain previously imported runs/wellness data", value=True)
            col1, col2 = st.columns(2)
            if col1.button("Confirm disconnect"):
                with session_scope() as gs:
                    res = garmin_accounts.disconnect_account(gs, get_app_user(gs), retain_imported_data=retain_data)
                st.session_state.pop("garmin_runs", None)
                st.session_state.pop("garmin_wellness", None)
                st.session_state.pop("_ai_plan_preview", None) # Fix Finding 4
                st.session_state.pop("confirm_disconnect", None)
                st.success("Garmin connection removed.")
                st.info(res["revocation_notice"])
                st.rerun()
            if col2.button("Cancel"):
                st.session_state.pop("confirm_disconnect", None)
                st.rerun()
    if credentials and st.button("Sync my Garmin runs"):
        try:
            with st.spinner("Contacting Garmin..."):
                with session_scope() as gs:
                    g_user = get_app_user(gs)
                    runs = garmin_accounts.fetch_account_runs(gs, g_user)
                    st.session_state["garmin_wellness"] = garmin_accounts.fetch_account_wellness(gs, g_user)
                    linked = auto_link(gs, g_user, runs)
                st.session_state["garmin_runs"] = runs
            st.success(f"Synced {len(runs)} runs; linked {linked} to your plan.")
        except Exception as exc:
            st.error(f"Garmin sync failed: {exc}")
elif st.button("Sync last 30 days from Garmin"):
    try:
        with st.spinner("Contacting Garmin..."):
            credentials = garmin_credentials()
            runs = fetch_runs(*credentials)
            from services.garmin_sync import fetch_wellness
            st.session_state["garmin_wellness"] = fetch_wellness(*credentials)
            st.session_state["garmin_runs"] = runs
            st.success(f"Synced {len(runs)} runs and recent available recovery metrics.")
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