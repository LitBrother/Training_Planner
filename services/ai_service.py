import json
import time
from datetime import date, timedelta

import requests
import truststore

from pydantic import ValidationError
from sqlalchemy import select

from models.orm import AIAdjustment, Event, Note, RunningSession, TrainingWeek
from models.schemas import AIProposal
from services import coaching, plan_service as ps
from services.adjustments import INTENSITY, rank
from services.strength_progress import est_1rm
from utils.dates import monday_of

truststore.inject_into_ssl()

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SYSTEM_PROMPT = """You are a cautious marathon-coaching assistant reviewing a runner's plan.
Return ONLY JSON: {"summary": str, "proposals": [{"session_date": "YYYY-MM-DD", "kind":
"reduce_volume|simplify_quality|increase_recovery|increase_volume|restore_volume|maintain",
"new_km": number, "new_type": string|null, "reason": str}]}.
Rules: cautiously increase or reduce volume, or simplify intensity; never move sessions or remove the taper.
Never exceed 110% of original session or weekly planned km, or the configured peak long-run distance.
Never increase mileage in taper or race week. Do not recommend increases for race/event sessions.
Only reference sessions listed in 'upcoming'. Do not give medical advice; for pain or injury recommend a
qualified professional. If no change is needed return an empty proposals list.
Use 'recent_runs' (real Garmin data: km, minutes, pace in min/km, avg HR), 'load', recent weekly completion,
training notes, and strength_history to justify coaching. Compare actual weekly km to planned. Mention easy-run
pace/HR drift, missed sessions, and strength progression where relevant. Cite numbers in each reason."""


def upcoming_sessions(s, user, today: date, days: int = 14) -> dict[date, dict]:
    weeks = list(s.scalars(select(TrainingWeek).where(TrainingWeek.user_id == user.id)))
    phases = {w.id: w.phase for w in weeks}
    week_totals = {w.id: w.planned_km for w in weeks}
    cfg = ps.get_settings(s, user)
    q = select(RunningSession).where(RunningSession.user_id == user.id, RunningSession.date >= today,
                                     RunningSession.date <= today + timedelta(days=days),
                                     RunningSession.status == "planned").order_by(RunningSession.date)
    return {r.date: {"date": r.date.isoformat(), "name": r.name, "slot": r.slot,
                     "type": r.adjusted_type or r.run_type, "planned_km": r.planned_km,
                     "adjusted_km": r.adjusted_km, "phase": phases.get(r.week_id, ""),
                     "weekly_planned_km": week_totals.get(r.week_id, 0),
                     "weekly_adjusted_km": next((w.adjusted_km for w in weeks if w.id == r.week_id), 0),
                     "peak_long_km": cfg.peak_long_km}
            for r in s.scalars(q)}


def build_context(s, user, today: date, garmin_runs: list[dict] | None = None) -> dict:
    weeks = [w for w in ps.list_weeks(s, user) if w.week_start < monday_of(today)][-4:]
    events = [e for e in ps.list_events(s, user) if today <= e.date <= today + timedelta(days=56)]
    notes = [n for n in ps.list_notes(s, user) if n.date >= today - timedelta(days=21)]
    ctx = {
        "today": today.isoformat(),
        "recent_weeks": [{"week_start": w.week_start.isoformat(), "planned": w.planned_km, "adjusted": w.adjusted_km,
                          "actual": w.actual_km, "completion_pct": w.completion_pct} for w in weeks],
        "events": [{"name": e.name, "date": e.date.isoformat(), "km": e.distance_km, "priority": e.priority}
                   for e in events],
        "notes": [{"date": n.date.isoformat(), "category": n.category, "text": n.text} for n in notes],
        "upcoming": list(upcoming_sessions(s, user, today).values()),
        "strength_history": strength_history(s, user, today),
    }
    if garmin_runs:
        ctx["recent_runs"] = coaching.compact_runs(garmin_runs, today)
        ctx["load"] = coaching.analyze(garmin_runs, today)
    return ctx


def strength_history(s, user, today: date) -> list[dict]:
    from models.orm import StrengthLog

    cutoff = today - timedelta(days=56)
    logs = list(s.scalars(select(StrengthLog).where(
        StrengthLog.user_id == user.id, StrengthLog.date >= cutoff, StrengthLog.date <= today
    ).order_by(StrengthLog.date)))
    grouped: dict[str, list] = {}
    for log in logs:
        grouped.setdefault(log.exercise, []).append(log)
    return [
        {
            "exercise": exercise,
            "sessions": len(rows),
            "latest": {"date": rows[-1].date.isoformat(), "weight_kg": rows[-1].weight_kg,
                       "sets": rows[-1].sets, "reps_per_set": rows[-1].reps,
                       "estimated_1rm_kg": est_1rm(rows[-1].weight_kg, rows[-1].reps)},
            "best_estimated_1rm_kg": max(est_1rm(row.weight_kg, row.reps) for row in rows),
        }
        for exercise, rows in grouped.items()
    ]


def request_proposals(context: dict, api_key: str, model: str) -> tuple[str, list[dict]]:
    if not model.startswith("gemini"):
        model = "gemini-3.5-flash-lite"
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": json.dumps(context, default=str)}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }
    resp = None
    for attempt in range(3):
        try:
            resp = requests.post(
                GEMINI_URL.format(model=model),
                headers={"Content-Type": "application/json", "X-goog-api-key": api_key},
                json=payload,
                timeout=(10, 90),
            )
        except requests.exceptions.Timeout:
            if attempt == 2:
                raise TimeoutError("Gemini timed out three times. Try again shortly or pick another model in Settings.")
            time.sleep(2 * (attempt + 1))
            continue
        if resp.status_code in (429, 500, 503) and attempt < 2:
            time.sleep(2 * (attempt + 1))
            continue
        break
    assert resp is not None
    if resp.status_code == 503:
        raise RuntimeError("Gemini is overloaded (503). Wait a minute and try again, or pick another model in Settings.")
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini error {resp.status_code}: {resp.text[:300]}")
    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    data = json.loads(text or "{}")
    return str(data.get("summary", "")), list(data.get("proposals", []))


def validate_proposals(raw: list, upcoming: dict[date, dict]) -> tuple[list[AIProposal], list[str]]:
    """Accept safe changes to upcoming sessions while preserving weekly and long-run limits."""
    ok: list[AIProposal] = []
    errs: list[str] = []
    seen: set[date] = set()
    for i, item in enumerate(raw, 1):
        try:
            p = AIProposal.model_validate(item)
        except ValidationError as e:
            errs.append(f"#{i}: invalid proposal ({e.errors()[0]['msg']})")
            continue
        info = upcoming.get(p.session_date)
        if info is None:
            errs.append(f"#{i}: {p.session_date} is not an upcoming planned session")
        elif info["slot"] == "race":
            errs.append(f"#{i}: race day cannot be changed")
        elif info["type"] == "Event":
            errs.append(f"#{i}: event sessions cannot be changed")
        elif p.new_km > float(info.get("planned_km", info["adjusted_km"])) * 1.1 + 1e-9:
            errs.append(f"#{i}: exceeds 110% of the session's planned distance")
        elif info.get("phase", "") in {"Taper", "Race Week"} and p.new_km > info["adjusted_km"] + 1e-9:
            errs.append(f"#{i}: mileage cannot be increased during taper or race week")
        elif info["slot"] == "long" and p.new_km > float(info.get("peak_long_km", 38)) + 1e-9:
            errs.append(f"#{i}: exceeds the configured peak long-run distance")
        elif p.new_type is not None and (p.new_type not in INTENSITY or rank(p.new_type) > rank(info["type"])):
            errs.append(f"#{i}: new type '{p.new_type}' is not an easier workout")
        elif p.session_date in seen:
            errs.append(f"#{i}: duplicate proposal for {p.session_date}")
        elif p.new_km == info["adjusted_km"] and p.new_type in (None, info["type"]):
            errs.append(f"#{i}: no change proposed")
        else:
            seen.add(p.session_date)
            ok.append(p)
    by_week: dict[date, list[AIProposal]] = {}
    for p in ok:
        by_week.setdefault(monday_of(p.session_date), []).append(p)
    for week_start, changes in by_week.items():
        first = upcoming[changes[0].session_date]
        week_cap = float(first.get("weekly_planned_km", first.get("planned_km", first["adjusted_km"]))) * 1.1
        current_total = float(first.get("weekly_adjusted_km", first["adjusted_km"]))
        replaced = sum(float(upcoming[p.session_date]["adjusted_km"]) for p in changes)
        proposed_total = current_total - replaced + sum(p.new_km for p in changes)
        if proposed_total > week_cap + 1e-9:
            for p in changes:
                ok.remove(p)
                errs.append(f"{p.session_date}: proposals exceed 110% of planned weekly distance")
    return ok, errs


def save_proposals(s, user, proposals: list[AIProposal], upcoming: dict[date, dict]) -> int:
    safe, errors = validate_proposals([p.model_dump(mode="json") for p in proposals], upcoming)
    if errors or len(safe) != len(proposals):
        raise ValueError("AI adjustments failed safety checks: " + "; ".join(errors))
    for p in safe:
        info = upcoming[p.session_date]
        s.add(AIAdjustment(user_id=user.id, session_date=p.session_date, kind=p.kind, old_km=info["adjusted_km"],
                           new_km=p.new_km, old_type=info["type"], new_type=p.new_type or info["type"],
                           reason=p.reason,
                           status="applied"))
    s.flush()
    ps.recalc(s, user)
    return len(proposals)


def set_status(s, user, adj_id: int, status: str, today: date | None = None) -> None:
    if status not in {"approved", "rejected", "reverted"}:
        raise ValueError("Invalid adjustment status.")
    a = s.get(AIAdjustment, adj_id)
    if a is None or a.user_id != user.id:
        raise ValueError("AI adjustment not found for this account.")
    a.status = status
    s.flush()
    ps.recalc(s, user, today)