import os

import streamlit as st
from utils.style import inject_css

from database.session import configure, session_scope
from services import plan_service as ps
from services.garmin_link import auto_link
from services.garmin_sync import fetch_runs_cached, garmin_credentials


@st.cache_resource
def _boot() -> bool:
    configure()
    with session_scope() as s:
        user = ps.ensure_user(s)
        ps.ensure_defaults(s, user)
        ps.sync_plan(s, user, ps.get_settings(s, user))
    return True


def ready() -> None:
    inject_css()
    _boot()
    auto_garmin_sync()


def get_openai_key() -> str | None:
    """Returns the AI API key (Gemini). Name kept so the pages don't change."""
    key = st.session_state.get("openai_key")
    if key:
        return key
    try:
        key = st.secrets.get("GEMINI_API_KEY")
    except Exception:
        key = None
    return key or os.getenv("GEMINI_API_KEY")


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
        st.session_state["garmin_msg"] = f"Garmin synced: {len(runs)} runs, {n} newly matched."
    except Exception as exc:
        st.session_state["garmin_msg"] = f"Garmin auto-sync failed: {exc}"