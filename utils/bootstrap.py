import os
from datetime import datetime

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError
from utils.style import inject_css

from database.session import configure, session_scope
from services import auth
from services.auth import AuthError
from services import plan_service as ps
from services.athlete_profile import get_profile
from services.garmin_link import auto_link
from services.garmin_sync import fetch_runs_cached, garmin_credentials


@st.cache_resource
def _boot(database_url: str | None) -> bool:
    configure(database_url)
    return True


def ready() -> None:
    inject_css()
    try:
        database_url, _, anon_key = auth.runtime_config()
    except AuthError as exc:
        st.error(str(exc))
        st.stop()
    _boot(database_url)
    if database_url and anon_key and not auth.render_login(anon_key):
        st.stop()

    identity = auth.current_identity(anon_key) if database_url and anon_key else None
    identity_key = identity[0] if identity else "local"
    if st.session_state.get("_initialized_identity") != identity_key:
        for key in (
            "garmin_runs", "garmin_wellness", "garmin_msg", "garmin_sync_last_success",
            "garmin_sync_run_count", "garmin_sync_error", "ai_summary", "ai_errors",
            "ai_strength_advice", "ai_strength_errors", "openai_key",
            "ai_notes_opt_in", "_ai_notes_request_opt_in", "_ai_notes_request_identity",
            "_ai_notes_consent_identity",
        ):
            st.session_state.pop(key, None)
        with session_scope() as s:
            user = ps.ensure_user(
                s, auth_uid=identity[0] if identity else None,
                email=identity[1] if identity else None,
            )
            ps.ensure_defaults(s, user)
            get_profile(s, user)
            ps.sync_plan(s, user, ps.get_settings(s, user))
        st.session_state["_initialized_identity"] = identity_key
    if not database_url:
        auto_garmin_sync()


def get_app_user(s):
    try:
        database_url, _, anon_key = auth.runtime_config()
    except AuthError as exc:
        raise RuntimeError(str(exc)) from exc
    if database_url:
        identity = auth.current_identity(anon_key or "")
        if identity is None:
            raise RuntimeError("Your sign-in session expired. Please sign in again.")
        return ps.ensure_user(s, auth_uid=identity[0], email=identity[1])
    return ps.ensure_user(s)


def get_openai_key() -> str | None:
    """Returns the AI API key (Gemini). Name kept so the pages don't change."""
    key = st.session_state.get("openai_key")
    if key:
        return key
    try:
        key = st.secrets.get("GEMINI_API_KEY")
    except StreamlitSecretNotFoundError:
        key = None
    return key or os.getenv("GEMINI_API_KEY")


from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectNotFoundError,
    GarminConnectTooManyRequestsError,
)

# ... (rest of imports)

def auto_garmin_sync() -> None:
    """Once per browser session: pull Garmin runs, link them to sessions, fill actual km."""
    if st.session_state.get("_garmin_tried"):
        return
    st.session_state["_garmin_tried"] = True  # never retry on every rerun (avoids login blocks)
    try:
        email, password = garmin_credentials()
    except RuntimeError:
        return
    try:
        runs = fetch_runs_cached(email, password)
        with session_scope() as s:
            n = auto_link(s, ps.ensure_user(s), runs)
        st.session_state["garmin_runs"] = runs
        st.session_state["garmin_sync_last_success"] = datetime.now()
        st.session_state["garmin_sync_run_count"] = len(runs)
        st.session_state.pop("garmin_sync_error", None)
        st.session_state["garmin_msg"] = f"Garmin synced: {len(runs)} runs, {n} newly matched."
    except (GarminConnectAuthenticationError, GarminConnectTooManyRequestsError,
            GarminConnectConnectionError, GarminConnectNotFoundError):
        msg = "Sync failed. Check your connection and Garmin credentials."
        st.session_state["garmin_sync_error"] = msg
        st.session_state["garmin_msg"] = msg
    except Exception:
        # log the real exception server-side, show nothing sensitive to the user
        msg = "Sync failed. Check your connection and Garmin credentials."
        st.session_state["garmin_sync_error"] = msg
        st.session_state["garmin_msg"] = msg
