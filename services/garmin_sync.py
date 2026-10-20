from datetime import date, timedelta
from pathlib import Path

import streamlit as st
import truststore
from garminconnect import Garmin, GarminConnectNotFoundError

from services.auth import runtime_config

truststore.inject_into_ssl()

TOKEN_DIR = Path(__file__).resolve().parent.parent / ".garmin_tokens"


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_runs_cached(email: str, password: str, days: int = 35) -> list[dict]:
    return fetch_runs(email, password, days)


def fetch_runs(email: str, password: str, days: int = 30, token_dir: Path | None = None) -> list[dict]:
    client = Garmin(email, password)
    token_path = token_dir or TOKEN_DIR
    token_path.mkdir(parents=True, exist_ok=True)
    client.login(str(token_path))  # reuses saved tokens, saves them after a fresh login
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


def _compact_wellness(payload, measure_keys: set[str]) -> list[dict]:
    by_date: dict[str, dict] = {}

    def walk(value, current_date: str = "recent") -> None:
        if isinstance(value, dict):
            found_date = next(
                (str(value[k])[:10] for k in ("calendarDate", "date") if value.get(k)),
                current_date,
            )
            row = by_date.setdefault(found_date, {"date": found_date})
            for key, item in value.items():
                if key in measure_keys and isinstance(item, (str, int, float, bool)):
                    row[key] = item
                elif isinstance(item, (dict, list)):
                    walk(item, found_date)
        elif isinstance(value, list):
            for item in value:
                walk(item, current_date)

    walk(payload)
    return [by_date[key] for key in sorted(by_date)][-14:]


def fetch_wellness(email: str, password: str, days: int = 7,
                   token_dir: Path | None = None) -> dict:
    client = Garmin(email, password)
    token_path = token_dir or TOKEN_DIR
    token_path.mkdir(parents=True, exist_ok=True)
    client.login(str(token_path))
    end = date.today()
    start = end - timedelta(days=max(1, days - 1))
    start_s, end_s = start.isoformat(), end.isoformat()
    metrics = {}
    unavailable = []
    endpoints = [
        ("sleep", lambda: client.get_sleep_daily(start_s, end_s),
         {"totalSleepSeconds", "sleepTimeSeconds", "sleepScore", "averageSpO2", "deepSleepSeconds",
          "lightSleepSeconds", "remSleepSeconds", "awakeSleepSeconds", "sleepDurationSeconds"}),
        ("hrv", lambda: client.get_hrv_data_range(start_s, end_s),
         {"lastNightAvg", "weeklyAvg", "baseline", "status", "hrvValue"}),
        ("training_readiness", lambda: client.get_training_readiness_range(start_s, end_s),
         {"score", "trainingReadinessScore", "recoveryTime", "inputContext", "sleepScore"}),
        ("stress", lambda: client.get_daily_stress_stats(start_s, end_s),
         {"overallStressLevel", "averageStressLevel", "maxStressLevel", "stressDuration", "restDuration"}),
        ("body_battery", lambda: client.get_daily_body_battery_stats(start_s, end_s),
         {"charged", "drained", "bodyBatteryMostRecentValue", "bodyBatteryAtWakeTime",
          "lowestBodyBatteryValue", "highestBodyBatteryValue"}),
    ]
    for name, fetch, keys in endpoints:
        try:
            result = fetch()
        except GarminConnectNotFoundError:
            unavailable.append(name)
            continue
        if result:
            metrics[name] = _compact_wellness(result, keys)
    try:
        resting = client.get_week_average_resting_hr(end_s)
    except GarminConnectNotFoundError:
        unavailable.append("resting_heart_rate")
    else:
        if resting:
            metrics["resting_heart_rate"] = _compact_wellness(
                resting, {"weeklyAverage", "last7DaysAvg", "restingHeartRate"}
            )
    return {"period": {"start": start_s, "end": end_s}, "metrics": metrics, "unavailable": unavailable}


def garmin_credentials() -> tuple[str, str]:
    if runtime_config()[0] is not None:
        raise RuntimeError("Shared Garmin credentials are disabled in hosted multi-account mode.")
    email = str(st.secrets.get("GARMIN_EMAIL", ""))
    password = str(st.secrets.get("GARMIN_PASSWORD", ""))
    if not email or not password:
        raise RuntimeError("Add GARMIN_EMAIL and GARMIN_PASSWORD to .streamlit/secrets.toml")
    return email, password