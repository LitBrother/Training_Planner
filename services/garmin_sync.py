from datetime import date, timedelta
from pathlib import Path

import streamlit as st
import truststore
from garminconnect import Garmin

from services.auth import runtime_config

truststore.inject_into_ssl()

TOKEN_DIR = Path(__file__).resolve().parent.parent / ".garmin_tokens"


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_runs_cached(email: str, password: str, days: int = 35) -> list[dict]:
    return fetch_runs(email, password, days)


def fetch_runs(email: str, password: str, days: int = 30) -> list[dict]:
    client = Garmin(email, password)
    client.login(str(TOKEN_DIR))  # reuses saved tokens, saves them after a fresh login
    end = date.today()
    start = end - timedelta(days=days)
    acts = client.get_activities_by_date(start.isoformat(), end.isoformat(), "running")
    return [
        {
            "date": date.fromisoformat(a["startTimeLocal"][:10]),
            "distance_km": round(a["distance"] / 1000, 2),
            "duration_min": round(a["duration"] / 60, 1),
            "avg_hr": int(a["averageHR"]) if a.get("averageHR") else None,
            "name": a.get("activityName", ""),
            "id": str(a["activityId"]),
        }
        for a in acts
    ]


def garmin_credentials() -> tuple[str, str]:
    if runtime_config()[0] is not None:
        raise RuntimeError("Shared Garmin credentials are disabled in hosted multi-account mode.")
    email = str(st.secrets.get("GARMIN_EMAIL", ""))
    password = str(st.secrets.get("GARMIN_PASSWORD", ""))
    if not email or not password:
        raise RuntimeError("Add GARMIN_EMAIL and GARMIN_PASSWORD to .streamlit/secrets.toml")
    return email, password