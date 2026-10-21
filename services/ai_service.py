import json
import time
from datetime import date, timedelta

import requests
import truststore

from pydantic import ValidationError
from sqlalchemy import select

from models.orm import AIAdjustment, Event, Note, RunningSession, TrainingWeek
from models.schemas import AIProposal
from services import coaching, evidence, plan_service as ps
from services.athlete_profile import get_profile
from services.adjustments import INTENSITY, rank
from services.strength_progress import est_1rm
from utils.dates import monday_of

truststore.inject_into_ssl()

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class AIRequestError(RuntimeError):
    """Sanitized provider failure safe to surface without echoing request data."""


SYSTEM_PROMPT = """You are a cautious, evidence-informed exercise coach.
Adapt the guide to the athlete's selected activity focus, experience, and stated goal. Do not presume
they are training for a marathon. Return ONLY JSON: {"summary": str, "proposals": [{"session_date": "YYYY-MM-DD", "kind":
"reduce_volume|simplify_quality|increase_recovery|increase_volume|restore_volume|maintain",
"new_km": number, "new_type": string|null, "reason": str}], "strength_advice": [{"exercise": str,
"next_weight_kg": number, "target_reps": int, "reason": str, "evidence_ids": [str]}]}.
Rules: cautiously increase or reduce volume, or simplify intensity; never move sessions or remove the taper.
Never exceed 110% of original session or weekly planned km, or the configured peak long-run distance.
Never increase mileage in taper or race week. Do not recommend increases for race/event sessions.
Only reference sessions listed in 'upcoming'. Do not give medical advice; for pain or injury recommend a
qualified professional. If no change is needed return an empty proposals list.
If the athlete mentions pain, injury, or potential overtraining, be conservative: withhold any increases in volume or intensity, and advise consulting a medical professional. Treat ambiguous or missed injury signals as uncertain; never treat them as evidence of being injury-free.
Use 'recent_runs' (real Garmin data: km, minutes, pace in min/km, avg HR), 'load', recent weekly completion,
training notes when supplied, and strength_history to justify coaching. Compare actual weekly km to planned. Mention easy-run
pace/HR drift, missed sessions, and strength progression where relevant. Use only evidence_ids supplied under
research_sources; do not invent studies, citations, or numerical prescriptions attributed to research. For each recommendation, provide the citation and the underlying principle from the allowed research_sources. For
opt-in Garmin sleep, HRV, resting-heart-rate, stress, battery, or readiness data, discuss multi-day patterns
cautiously; consumer wearables are not medical diagnostics and a single score must not drive a plan change. For
strength advice, use only exercises the athlete logged, never suggest >10% above their latest load, and only
recommend adding load when the logged repetitions suggest the current load is manageable. State that the athlete
should increase only if the target reps were completed with good form and without pain. Do not infer fitness age,
health status, or training potential from age or body weight; do not prescribe weight from body weight."""


def athlete_context(s, user) -> dict:
    profile = get_profile(s, user)
    return {
        "training_focus": profile.training_focus,
        "experience_level": profile.experience_level,
        "goal": profile.goal,
    }


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


def build_context(s, user, today: date, garmin_runs: list[dict] | None = None,
                  garmin_wellness: dict | None = None, include_notes: bool = False,
                  include_garmin_recovery: bool = False) -> dict:
    weeks = [w for w in ps.list_weeks(s, user) if w.week_start < monday_of(today)][-4:]
    events = [e for e in ps.list_events(s, user) if today <= e.date <= today + timedelta(days=56)]
    profile = get_profile(s, user)
    ctx = {
        "today": today.isoformat(),
        "recent_weeks": [{"week_start": w.week_start.isoformat(), "planned": w.planned_km, "adjusted": w.adjusted_km,
                          "actual": w.actual_km, "completion_pct": w.completion_pct} for w in weeks],
        "events": [{"name": e.name, "date": e.date.isoformat(), "km": e.distance_km, "priority": e.priority}
                   for e in events],
        "upcoming": list(upcoming_sessions(s, user, today).values()),
        "strength_history": strength_history(s, user, today),
        # Age, body weight, and persistent recovery-sharing preferences are not part of
        # a review context. A recovery choice is always made afresh for this request.
        "athlete_profile": {
            "training_focus": profile.training_focus,
            "experience_level": profile.experience_level,
            "goal": profile.goal,
        },
        "research_sources": evidence.SOURCES,
    }
    if include_notes:
        notes = [n for n in ps.list_notes(s, user) if n.date >= today - timedelta(days=21)]
        ctx["notes"] = [
            {"date": n.date.isoformat(), "category": n.category, "text": n.text}
            for n in notes
        ]
    if garmin_runs:
        ctx["recent_runs"] = coaching.compact_runs(garmin_runs, today)
        ctx["load"] = coaching.analyze(garmin_runs, today)
    if include_garmin_recovery and garmin_wellness:
        ctx["garmin_wellness"] = _compact_garmin_wellness(garmin_wellness)
    return ctx


_WELLNESS_FIELDS = {
    "sleep": {
        "totalSleepSeconds", "sleepTimeSeconds", "sleepScore", "averageSpO2",
        "deepSleepSeconds", "lightSleepSeconds", "remSleepSeconds",
        "awakeSleepSeconds", "sleepDurationSeconds",
    },
    "hrv": {"lastNightAvg", "weeklyAvg", "baseline", "status", "hrvValue"},
    "training_readiness": {"score", "trainingReadinessScore", "recoveryTime", "sleepScore"},
    "stress": {"overallStressLevel", "averageStressLevel", "maxStressLevel", "stressDuration", "restDuration"},
    "body_battery": {
        "charged", "drained", "bodyBatteryMostRecentValue", "bodyBatteryAtWakeTime",
        "lowestBodyBatteryValue", "highestBodyBatteryValue",
    },
    "resting_heart_rate": {"weeklyAverage", "last7DaysAvg", "restingHeartRate"},
}


def _compact_garmin_wellness(data: dict) -> dict:
    """Project Garmin results onto a small, known, non-identifier metric allowlist."""
    raw_metrics = data.get("metrics")
    if not isinstance(raw_metrics, dict):
        return {}
    metrics: dict[str, list[dict]] = {}
    for category, allowed_fields in _WELLNESS_FIELDS.items():
        rows = raw_metrics.get(category)
        if not isinstance(rows, list):
            continue
        compact_rows = []
        for row in rows[-14:]:
            if not isinstance(row, dict):
                continue
            compact = {}
            if isinstance(row.get("date"), str):
                compact["date"] = row["date"][:10]
            for field in allowed_fields:
                value = row.get(field)
                if isinstance(value, (str, int, float, bool)):
                    compact[field] = value
            if any(key != "date" for key in compact):
                compact_rows.append(compact)
        if compact_rows:
            metrics[category] = compact_rows
    return {"metrics": metrics} if metrics else {}


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


def request_proposals(context: dict, api_key: str, model: str,
                      include_notes: bool = False,
                      include_garmin_recovery: bool = False) -> tuple[str, list[dict], list[dict]]:
    if not model.startswith("gemini"):
        model = "gemini-3.5-flash-lite"
    # Treat the affirmative as request-scoped: callers cannot accidentally forward notes
    # already present in a cached/reused context without passing this explicit opt-in.
    # A request is a fresh, explicit allowlist projection. In particular this drops
    # identifiers, tokens, raw provider objects, age, and body weight even if a caller
    # accidentally puts them in a reusable context dictionary.
    top_level = {
        "today", "recent_weeks", "events", "upcoming", "strength_history",
        "athlete_profile", "research_sources", "recent_runs", "load", "notes",
        "garmin_wellness",
    }
    request_context = {key: context[key] for key in top_level if key in context}
    if not include_notes:
        request_context.pop("notes", None)
    if not include_garmin_recovery:
        request_context.pop("garmin_wellness", None)
    request_context = _project_request_context(request_context)
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": json.dumps(request_context, default=str)}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }
    resp = None
    for attempt in range(5):
        try:
            resp = requests.post(
                GEMINI_URL.format(model=model),
                headers={"Content-Type": "application/json", "X-goog-api-key": api_key},
                json=payload,
                timeout=(10, 90),
            )
        except requests.exceptions.Timeout:
            if attempt == 4:
                raise AIRequestError(
                    "Gemini timed out. Try again shortly or pick another model in Settings."
                ) from None
            time.sleep(2 ** attempt)
            continue
        except requests.exceptions.RequestException:
            raise AIRequestError("Gemini request failed. Please try again.") from None
        if resp.status_code in (429, 500, 503) and attempt < 4:
            time.sleep(2 ** attempt)
            continue
        break
    assert resp is not None
    if resp.status_code == 503:
        raise AIRequestError(
            "Gemini is overloaded (503). Wait a minute and try again, or pick another model in Settings."
        )
    if resp.status_code != 200:
        # Provider response bodies can echo user-supplied prompt data; never include them in errors.
        raise AIRequestError(f"Gemini request failed with status {resp.status_code}.")
    try:
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        data = json.loads(text or "{}")
        return (
            str(data.get("summary", "")),
            list(data.get("proposals", [])),
            list(data.get("strength_advice", [])),
        )
    except (requests.exceptions.RequestException, ValueError, KeyError, IndexError, TypeError, AttributeError):
        # Parser exceptions may include an echoed provider response, so expose only a fixed message.
        raise AIRequestError("Gemini returned an invalid response. Please try again.") from None


def validate_strength_advice(raw: list, history: list[dict]) -> tuple[list[dict], list[str]]:
    known = {row["exercise"].casefold(): row for row in history}
    verified_sources = {source["id"] for source in evidence.SOURCES}
    accepted: list[dict] = []
    errors: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            errors.append(f"Strength suggestion #{index}: expected an object.")
            continue
        name = str(item.get("exercise", "")).strip()
        exercise = known.get(name.casefold())
        if exercise is None:
            errors.append(f"Strength suggestion #{index}: exercise has no logged training history.")
            continue
        key = exercise["exercise"].casefold()
        if key in seen:
            errors.append(f"Strength suggestion #{index}: duplicate exercise.")
            continue
        latest = exercise["latest"]
        try:
            weight = float(item["next_weight_kg"])
            reps = int(item["target_reps"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"Strength suggestion #{index}: invalid target weight or reps.")
            continue
        if latest["weight_kg"] <= 0 or weight < 0 or weight > latest["weight_kg"] * 1.1 + 1e-9:
            errors.append(f"Strength suggestion #{index}: target load exceeds the 10% progression limit.")
            continue
        if not 1 <= reps <= 15:
            errors.append(f"Strength suggestion #{index}: target reps must be between 1 and 15.")
            continue
        ids = item.get("evidence_ids", [])
        if not isinstance(ids, list) or any(value not in verified_sources for value in ids):
            errors.append(f"Strength suggestion #{index}: contains an unverified research citation.")
            continue
        accepted.append({
            "exercise": exercise["exercise"],
            "latest_weight_kg": latest["weight_kg"],
            "next_weight_kg": round(weight, 2),
            "target_reps": reps,
            "reason": str(item.get("reason", ""))[:500],
            "evidence_ids": ids,
        })
        seen.add(key)
    return accepted, errors


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


def _project_request_context(context: dict) -> dict:
    """Remove unexpected nested provider fields from the allowlisted request categories."""
    row_fields = {
        "recent_weeks": {"week_start", "planned", "adjusted", "actual", "completion_pct"},
        "events": {"name", "date", "km", "priority"},
        "upcoming": {
            "date", "name", "slot", "type", "planned_km", "adjusted_km", "phase",
            "weekly_planned_km", "weekly_adjusted_km", "peak_long_km",
        },
        "recent_runs": {"date", "km", "min", "pace", "hr"},
        "notes": {"date", "category", "text"},
    }
    projected = {}
    if isinstance(context.get("today"), str):
        projected["today"] = context["today"]
    for category, allowed in row_fields.items():
        rows = context.get(category)
        if isinstance(rows, list):
            projected[category] = [
                {key: row[key] for key in allowed if isinstance(row, dict) and key in row
                 and isinstance(row[key], (str, int, float, bool, type(None)))}
                for row in rows
            ]
    profile = context.get("athlete_profile")
    if isinstance(profile, dict):
        projected["athlete_profile"] = {
            key: profile[key] for key in ("training_focus", "experience_level", "goal")
            if isinstance(profile.get(key), str)
        }
    numeric_load = {"acute_km", "prev_week_km", "chronic_weekly_km", "acwr", "reduce_pct"}
    load = context.get("load")
    if isinstance(load, dict):
        clean_load = {key: load[key] for key in numeric_load
                      if isinstance(load.get(key), (int, float, type(None)))}
        if isinstance(load.get("flags"), list):
            clean_load["flags"] = [item[:300] for item in load["flags"] if isinstance(item, str)][:20]
        if isinstance(load.get("easy_too_hard"), bool):
            clean_load["easy_too_hard"] = load["easy_too_hard"]
        projected["load"] = clean_load
    history = context.get("strength_history")
    if isinstance(history, list):
        fields = {"exercise", "sessions", "latest", "best_estimated_1rm_kg"}
        projected["strength_history"] = []
        for row in history:
            if not isinstance(row, dict):
                continue
            item = {
                key: row[key] for key in fields - {"latest"}
                if key in row and isinstance(row[key], (str, int, float, bool))
            }
            latest = row.get("latest")
            if isinstance(latest, dict):
                item["latest"] = {
                    key: latest[key] for key in (
                        "date", "weight_kg", "sets", "reps_per_set", "estimated_1rm_kg"
                    ) if key in latest and isinstance(latest[key], (str, int, float, bool, type(None)))
                }
            projected["strength_history"].append(item)
    sources = context.get("research_sources")
    if isinstance(sources, list):
        projected["research_sources"] = [
            {key: row[key] for key in ("id", "citation", "url", "principle")
             if isinstance(row, dict) and isinstance(row.get(key), str)}
            for row in sources if isinstance(row, dict)
        ]
    wellness = context.get("garmin_wellness")
    if isinstance(wellness, dict):
        projected["garmin_wellness"] = _compact_garmin_wellness(wellness)
    return projected


def create_preview(
    raw_proposals: list, upcoming: dict[date, dict], owner_id: int
) -> tuple[list[AIProposal], list[str], dict]:
    """Validate proposals and capture the current plan values shown to the athlete."""
    safe, errors = validate_proposals(raw_proposals, upcoming)
    sessions = {
        p.session_date.isoformat(): {
            key: upcoming[p.session_date].get(key)
            for key in (
                "slot", "type", "planned_km", "adjusted_km", "phase",
                "weekly_planned_km", "weekly_adjusted_km", "peak_long_km",
            )
        }
        for p in safe
    }
    snapshot = {"owner_id": owner_id, "sessions": sessions} if safe else {}
    return safe, errors, snapshot


def apply_reviewed_proposals(
    s, user, reviewed: list[dict], selected: list[dict], snapshot: dict,
    today: date | None = None,
) -> int:
    """Apply only explicitly selected proposals from a read-only review, if still current."""
    if not selected:
        raise ValueError("Select at least one reviewed change to confirm.")
    if not isinstance(snapshot, dict) or snapshot.get("owner_id") != user.id:
        raise ValueError("This preview is no longer valid. Request a new review.")
    session_snapshot = snapshot.get("sessions")
    if not isinstance(session_snapshot, dict):
        raise ValueError("This preview is no longer valid. Request a new review.")
    reviewed_models, reviewed_errors = validate_proposals(reviewed, {
        date.fromisoformat(key): {
            **value, "date": key, "name": "", "slot": value.get("slot", ""),
            "type": value.get("type", ""), "adjusted_km": value.get("adjusted_km", 0),
            "planned_km": value.get("planned_km", 0),
        }
        for key, value in session_snapshot.items() if isinstance(key, str) and isinstance(value, dict)
    })
    if reviewed_errors or len(reviewed_models) != len(reviewed):
        raise ValueError("This preview is invalid. Request a new review.")
    reviewed_by_json = {json.dumps(p.model_dump(mode="json"), sort_keys=True) for p in reviewed_models}
    selected_json = [json.dumps(item, sort_keys=True) for item in selected]
    if len(set(selected_json)) != len(selected_json) or any(item not in reviewed_by_json for item in selected_json):
        raise ValueError("Selected changes do not match the reviewed preview.")

    if ps.training_is_paused(s, user):
        raise ValueError("Training is paused. Resume training before applying plan changes.")
    current = upcoming_sessions(s, user, today or date.today())
    selected_models, errors = validate_proposals(selected, current)
    if errors or len(selected_models) != len(selected):
        raise ValueError("The plan changed or the preview no longer passes safety checks. Request a new review.")
    for proposal in selected_models:
        old = session_snapshot.get(proposal.session_date.isoformat())
        current_info = current.get(proposal.session_date)
        if not isinstance(old, dict) or current_info is None:
            raise ValueError("The plan changed. Request a new review before applying changes.")
        for key in (
            "slot", "type", "planned_km", "adjusted_km", "phase",
            "weekly_planned_km", "weekly_adjusted_km", "peak_long_km",
        ):
            previous, latest = old.get(key), current_info.get(key)
            if isinstance(previous, (int, float)) and isinstance(latest, (int, float)):
                if abs(float(previous) - float(latest)) > 1e-9:
                    raise ValueError("The plan changed. Request a new review before applying changes.")
            elif previous != latest:
                raise ValueError("The plan changed. Request a new review before applying changes.")

    # Recheck all selected proposals together before writing anything; savepoint ensures
    # a failure in plan recalculation cannot leave a partial application behind.
    with s.begin_nested():
        for proposal in selected_models:
            info = current[proposal.session_date]
            s.add(AIAdjustment(
                user_id=user.id, session_date=proposal.session_date, kind=proposal.kind,
                old_km=info["adjusted_km"], new_km=proposal.new_km,
                old_type=info["type"], new_type=proposal.new_type or info["type"],
                reason=proposal.reason, status="applied",
            ))
        s.flush()
        ps.recalc(s, user, today)
    return len(selected_models)


def save_pending_proposals(s, user, proposals: list[dict], upcoming: dict[date, dict]) -> list[int]:
    """Saves proposals as pending AIAdjustments without applying changes to sessions/weeks."""
    safe_models, _ = validate_proposals(proposals, upcoming)
    
    adjustment_ids = []
    for p in safe_models:
        info = upcoming[p.session_date]
        adj = AIAdjustment(
            user_id=user.id, session_date=p.session_date, kind=p.kind,
            old_km=info["adjusted_km"], new_km=p.new_km,
            old_type=info["type"], new_type=p.new_type or info["type"],
            reason=p.reason, status="pending",
        )
        s.add(adj)
        s.flush()
        adjustment_ids.append(adj.id)
    return adjustment_ids


def apply_pending_proposals(s, user, adjustment_ids: list[int], today: date | None = None) -> int:
    """Applies a list of pending AIAdjustments to the running plan."""
    adjustments = s.scalars(
        select(AIAdjustment).where(
            AIAdjustment.id.in_(adjustment_ids),
            AIAdjustment.user_id == user.id,
            AIAdjustment.status == "pending"
        )
    ).all()
    
    if len(adjustments) != len(adjustment_ids):
        raise ValueError("Some adjustments were not found or already applied.")
    
    # Apply all adjustments in a single nested transaction
    with s.begin_nested():
        for adj in adjustments:
            adj.status = "applied"
        s.flush()
        ps.recalc(s, user, today)
    
    return len(adjustments)


def set_status(s, user, adj_id: int, status: str, today: date | None = None) -> None:
    """Update adjustment status and optionally apply/revert changes."""
    a = s.get(AIAdjustment, adj_id)
    if a is None or a.user_id != user.id:
        raise ValueError("AI adjustment not found for this account.")
    
    if status == "rejected":
        if a.status != "pending":
            raise ValueError("Only an adjustment in pending status can be rejected.")
        a.status = status
    elif status == "approved":
        raise ValueError("Invalid adjustment status.")
    elif status == "reverted":
        if a.status != "applied":
            raise ValueError("Only an applied adjustment can be reverted.")
        a.status = status
        s.flush()
        ps.recalc(s, user, today)
    else:
        raise ValueError("Invalid adjustment status.")