import math
from datetime import date, datetime, time, timedelta, timezone
from numbers import Real

import pandas as pd
from sqlalchemy import func, select

from models.orm import (
    AIAdjustment, Event, Friendship, Note, RunCorrectionHistory, RunningSession, Setting,
    StrengthLog, StrengthSession, TrainingWeek, User,
)
from models.schemas import PlanSettings
from services.adjustments import INTENSITY, adjust_km, apply_event_week, completion_pct, rank, reduction_factor
from services.plan_generator import generate_plan
from utils.dates import monday_of

GOAL_NAME = "Goal Event"
LEGACY_GOAL_NAMES = {GOAL_NAME, "Goal Marathon"}
EVENT_TYPES = {"5km": 5.0, "10km": 10.0, "Half marathon": 21.1, "Marathon": 42.2, "Trail race": 0.0, "Custom": 0.0}
NOTE_CATEGORIES = ("Training", "Injury", "Travel", "Fatigue", "Sleep", "General")


class _UnsetRunContext:
    pass


_UNSET_RUN_CONTEXT = _UnsetRunContext()


# ---------------------------------------------------------------- users / settings
def ensure_user(
    s, name: str = "Athlete", auth_uid: str | None = None, email: str | None = None
) -> User:
    if auth_uid:
        u = s.scalars(select(User).where(User.auth_uid == auth_uid)).first()
        if u is None:
            # The first account claims the pre-auth single-user profile when one exists.
            u = s.scalars(select(User).where(User.auth_uid.is_(None)).order_by(User.id)).first()
            if u is None:
                profile_name = email.partition("@")[0].replace(".", " ").title() if email else name
                u = User(name=profile_name)
                s.add(u)
            u.auth_uid = auth_uid
            u.email = email
            s.flush()
        return u
    u = s.scalars(select(User).where(User.auth_uid.is_(None)).order_by(User.id)).first()
    if u is None:
        u = User(name=name)
        s.add(u)
        s.flush()
    return u


def _setting_rows(s, user) -> dict:
    return {r.key: r.value for r in s.scalars(select(Setting).where(Setting.user_id == user.id))}


def training_is_paused(s, user) -> bool:
    """Return this account's persisted training-pause state."""
    rows = s.scalars(select(Setting).where(
        Setting.user_id == user.id, Setting.key == "training_paused"
    )).all()
    return any(row.value == "true" for row in rows)


def set_training_paused(s, user, paused: bool) -> None:
    """Persist pause state without changing the plan or any logged sessions."""
    if not isinstance(paused, bool):
        raise ValueError("Training pause state must be true or false.")
    rows = s.scalars(select(Setting).where(
        Setting.user_id == user.id, Setting.key == "training_paused"
    )).all()
    if paused:
        if rows:
            rows[0].value = "true"
            for duplicate in rows[1:]:
                s.delete(duplicate)
        else:
            s.add(Setting(user_id=user.id, key="training_paused", value="true"))
    else:
        for row in rows:
            s.delete(row)
    s.flush()


def get_settings(s, user) -> PlanSettings:
    rows = _setting_rows(s, user)
    rows.setdefault("plan_start", monday_of(date.today()).isoformat())
    if not str(rows.get("openai_model", "")).startswith("gemini"):
        rows["openai_model"] = "gemini-3.5-flash-lite"
    return PlanSettings.model_validate(rows)


def save_settings(s, user, cfg: PlanSettings) -> None:
    existing = {r.key: r for r in s.scalars(select(Setting).where(Setting.user_id == user.id))}
    for k, v in cfg.model_dump().items():
        if v is None:
            if k in existing:
                s.delete(existing[k])
            continue
        if k in existing:
            existing[k].value = str(v)
        else:
            s.add(Setting(user_id=user.id, key=k, value=str(v)))
    user.name = cfg.user_name
    s.flush()


def ensure_defaults(s, user) -> None:
    if "plan_start" not in _setting_rows(s, user):
        save_settings(s, user, get_settings(s, user))


# ---------------------------------------------------------------- plan sync
def sync_plan(s, user, cfg: PlanSettings, today: date | None = None) -> None:
    """Regenerate plan from settings, preserving logged sessions."""
    plan = generate_plan(
        cfg.plan_start, cfg.end_date, cfg.peak_long_km, cfg.start_long_km,
        cfg.effective_taper, cfg.weekend_day, include_race_week=cfg.race_date is not None,
        training_focus=cfg.training_focus, race_distance_km=cfg.race_distance_km,
    )
    ew = {w.week_start: w for w in s.scalars(select(TrainingWeek).where(TrainingWeek.user_id == user.id))}
    er = {r.date: r for r in s.scalars(select(RunningSession).where(RunningSession.user_id == user.id))}
    es = {x.date: x for x in s.scalars(select(StrengthSession).where(StrengthSession.user_id == user.id))}
    keep_w, keep_r, keep_s = set(), set(), set()
    for wk in plan:
        tw = ew.get(wk["week_start"])
        if tw is None:
            tw = TrainingWeek(user_id=user.id, week_start=wk["week_start"])
            s.add(tw)
        tw.week_no, tw.phase, tw.is_recovery = wk["week_no"], wk["phase"], wk["is_recovery"]
        tw.planned_km = round(sum(r["km"] for r in wk["runs"]), 1)
        s.flush()
        keep_w.add(tw.week_start)
        for r in wk["runs"]:
            row = er.get(r["date"])
            if row is None:
                row = RunningSession(user_id=user.id, date=r["date"], status="planned")
                s.add(row)
            row.week_id, row.weekday, row.slot = tw.id, r["weekday"], r["slot"]
            row.name, row.run_type, row.variant, row.planned_km = r["name"], r["run_type"], r["variant"], r["km"]
            if row.status == "planned" and row.actual_km is None:
                row.adjusted_km, row.adjusted_type = r["km"], r["run_type"]
            keep_r.add(r["date"])
        for x in wk["strength"]:
            row = es.get(x["date"])
            if row is None:
                row = StrengthSession(user_id=user.id, date=x["date"], completed=False)
                s.add(row)
            row.week_id, row.weekday, row.kind = tw.id, x["weekday"], x["kind"]
            row.core_minutes, row.volume_level = x["core_minutes"], x["volume_level"]
            keep_s.add(x["date"])
    s.flush()
    for d, row in er.items():
        if d not in keep_r and row.status == "planned" and row.actual_km is None:
            s.delete(row)
    for d, row in es.items():
        if d not in keep_s and not row.completed:
            s.delete(row)
    s.flush()
    for ws, tw in ew.items():
        if ws in keep_w:
            continue
        has_run = s.scalars(select(RunningSession.id).where(RunningSession.week_id == tw.id)).first()
        has_str = s.scalars(select(StrengthSession.id).where(StrengthSession.week_id == tw.id)).first()
        if not has_run and not has_str:
            s.delete(tw)
    goal = s.scalars(select(Event).where(Event.user_id == user.id, Event.name == GOAL_NAME)).first()
    if cfg.race_date is None:
        if goal is not None:
            s.delete(goal)
    elif goal is None:
        s.add(Event(user_id=user.id, name=GOAL_NAME, event_type="Goal Event", date=cfg.race_date,
                    distance_km=cfg.race_distance_km, priority="A", notes="User goal event"))
    else:
        goal.date = cfg.race_date
        goal.distance_km = cfg.race_distance_km
        goal.event_type = "Goal Event"
    s.flush()
    recalc(s, user, today)


# ---------------------------------------------------------------- recalculation
def _week_runs(s, user):
    start = monday_of(get_settings(s, user).plan_start)
    weeks = list(s.scalars(select(TrainingWeek).where(TrainingWeek.user_id == user.id,
                                                      TrainingWeek.week_start >= start)
                           .order_by(TrainingWeek.week_start)))
    runs = list(s.scalars(select(RunningSession).where(RunningSession.user_id == user.id)
                          .order_by(RunningSession.date)))
    by: dict[int, list] = {}
    for r in runs:
        by.setdefault(r.week_id, []).append(r)
    return weeks, by


def week_completion(runs) -> float:
    return completion_pct(sum(r.adjusted_km for r in runs), sum(r.actual_km or 0 for r in runs))


def _current_factor(weeks, by, cur: date) -> float:
    for w in reversed(weeks):
        if w.week_start >= cur:
            continue
        rs = by.get(w.id, [])
        if any(r.status != "planned" for r in rs):
            return reduction_factor(week_completion(rs))
    return 1.0


def _week_event(events, ws: date):
    cands = [e for e in events if e.name not in LEGACY_GOAL_NAMES
             and ws <= e.date <= ws + timedelta(days=6) and e.distance_km]
    return max(cands, key=lambda e: e.distance_km, default=None)


def recalc(s, user, today: date | None = None) -> None:
    """Recompute current/future volume while enforcing plan, AI, and event limits."""
    today = today or date.today()
    cur = monday_of(today)
    weeks, by = _week_runs(s, user)
    events = list(s.scalars(select(Event).where(Event.user_id == user.id)))
    ai: dict[date, list] = {}
    for a in s.scalars(select(AIAdjustment).where(
        AIAdjustment.user_id == user.id, AIAdjustment.status.in_(("approved", "applied"))
    ).order_by(AIAdjustment.created_at, AIAdjustment.id)):
        ai[a.session_date] = [a]
    factor = _current_factor(weeks, by, cur)
    prev_long_missed = None
    cfg = get_settings(s, user)
    for w in weeks:
        rs = by.get(w.id, [])
        if w.week_start >= cur:
            rows = [{"slot": r.slot, "run_type": r.run_type, "km": r.planned_km} for r in rs]
            ev = _week_event(events, w.week_start)
            if ev and not any(r.slot == "race" for r in rs):
                rows = apply_event_week(rows, ev.distance_km)
            for r, row in zip(rs, rows):
                if r.status != "planned" or r.actual_km is not None:
                    continue
                if r.slot == "race":
                    r.adjusted_km, r.adjusted_type = r.planned_km, r.run_type
                    continue
                km, typ = row["km"], row["run_type"]
                is_event = row["slot"] == "weekend" and typ == "Event"
                if not is_event:
                    km = adjust_km(km, factor)
                    if r.slot == "long" and prev_long_missed is not None:
                        km = min(km, prev_long_missed)
                for a in ai.get(r.date, []):
                    max_km = min(r.planned_km * 1.1, cfg.peak_long_km) if r.slot == "long" else r.planned_km * 1.1
                    km = min(max(a.new_km, 0), max_km)
                    if a.new_type and rank(a.new_type) <= rank(typ):
                        typ = a.new_type
                if not is_event:
                    if not ai.get(r.date):
                        km = min(km, r.planned_km)
                r.adjusted_km, r.adjusted_type = round(km, 1), typ
        longs = [r for r in rs if r.slot == "long"]
        prev_long_missed = longs[0].planned_km if longs and longs[0].status == "missed" else None
        w.planned_km = round(sum(r.planned_km for r in rs), 1)
        w.adjusted_km = round(sum(r.adjusted_km for r in rs), 1)
        w.actual_km = round(sum(r.actual_km or 0 for r in rs), 1)
        w.completion_pct = week_completion(rs)
    s.flush()


# ---------------------------------------------------------------- queries
def list_weeks(s, user):
    start = monday_of(get_settings(s, user).plan_start)
    return list(s.scalars(select(TrainingWeek).where(
        TrainingWeek.user_id == user.id, TrainingWeek.week_start >= start).order_by(TrainingWeek.week_start)))


def week_runs(s, week_id, user=None):
    query = select(RunningSession).where(RunningSession.week_id == week_id)
    if user is not None:
        query = query.where(RunningSession.user_id == user.id)
    return list(s.scalars(query.order_by(RunningSession.date)))


def week_strength(s, week_id, user=None):
    query = select(StrengthSession).where(StrengthSession.week_id == week_id)
    if user is not None:
        query = query.where(StrengthSession.user_id == user.id)
    return list(s.scalars(query.order_by(StrengthSession.date)))


def weekly_activity_summary(s, user, week) -> dict:
    """Return persisted activity for one of the signed-in user's training weeks."""
    owned_week = s.scalars(select(TrainingWeek).where(
        TrainingWeek.id == week.id, TrainingWeek.user_id == user.id
    )).first()
    if owned_week is None:
        raise ValueError("Training week not found for this account.")

    runs = list(s.scalars(select(RunningSession).where(
        RunningSession.user_id == user.id, RunningSession.week_id == owned_week.id
    )))
    recorded_runs = [run for run in runs if run.status in ("completed", "missed")]
    strengths = list(s.scalars(select(StrengthSession).where(
        StrengthSession.user_id == user.id, StrengthSession.week_id == owned_week.id
    )))
    lift_log_count = s.scalar(select(func.count(StrengthLog.id)).where(
        StrengthLog.user_id == user.id,
        StrengthLog.date >= owned_week.week_start,
        StrengthLog.date <= owned_week.week_start + timedelta(days=6),
    )) or 0

    return {
        "has_running_records": bool(runs),
        "planned_running_km": round(sum(run.planned_km for run in runs), 1) if runs else None,
        "adjusted_running_km": round(sum(run.adjusted_km for run in runs), 1) if runs else None,
        "actual_running_km": round(sum(run.actual_km or 0 for run in recorded_runs), 1)
        if recorded_runs else None,
        "running_completion_pct": week_completion(runs) if recorded_runs else None,
        "completed_runs": sum(run.status == "completed" for run in runs),
        "missed_runs": sum(run.status == "missed" for run in runs),
        "planned_runs": sum(run.status == "planned" for run in runs),
        "planned_strength_sessions": len(strengths),
        "completed_strength_sessions": sum(session.completed for session in strengths),
        "lift_log_entries": lift_log_count,
    }


def all_runs(s, user):
    return list(s.scalars(select(RunningSession).where(RunningSession.user_id == user.id).order_by(RunningSession.date)))


def list_events(s, user):
    return list(s.scalars(select(Event).where(Event.user_id == user.id).order_by(Event.date)))


def list_notes(s, user):
    return list(s.scalars(select(Note).where(Note.user_id == user.id).order_by(Note.date.desc(), Note.id.desc())))


def list_adjustments(
    s,
    user,
    status: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
):
    q = select(AIAdjustment).where(AIAdjustment.user_id == user.id)
    if status:
        q = q.where(AIAdjustment.status == status)
    if start_date:
        q = q.where(AIAdjustment.created_at >= datetime.combine(start_date, time.min))
    if end_date:
        q = q.where(AIAdjustment.created_at < datetime.combine(end_date + timedelta(days=1), time.min))
    return list(s.scalars(q.order_by(AIAdjustment.created_at.desc(), AIAdjustment.id.desc())))


def run_correction_history(s, user, run_id: int) -> list[RunCorrectionHistory]:
    """Return corrections only when the requested run belongs to this account."""
    run = s.scalars(select(RunningSession).where(
        RunningSession.id == run_id, RunningSession.user_id == user.id
    )).first()
    if run is None:
        raise ValueError("Running session not found for this account.")
    return list(s.scalars(select(RunCorrectionHistory).where(
        RunCorrectionHistory.user_id == user.id,
        RunCorrectionHistory.running_session_id == run.id,
    ).order_by(RunCorrectionHistory.corrected_at, RunCorrectionHistory.id)))


def correct_run_distance(s, user, run_id: int, field_name: str, new_value: float) -> bool:
    """Correct a manually logged completed run, recording an atomic UTC audit entry.

    Returns False for an unchanged value. All changes are contained in a savepoint
    so a failed audit insert or recalculation leaves the run and derived totals intact.
    """
    if field_name != "actual_km":
        raise ValueError("Only actual_km can be corrected.")
    if not isinstance(new_value, Real) or isinstance(new_value, bool):
        raise ValueError("Actual distance must be a positive finite number up to 100 km.")
    try:
        new_distance = float(new_value)
    except OverflowError:
        raise ValueError("Actual distance must be a positive finite number up to 100 km.") from None
    if not math.isfinite(new_distance) or not 0 < new_distance <= 100:
        raise ValueError("Actual distance must be a positive finite number up to 100 km.")

    run = s.scalars(select(RunningSession).where(
        RunningSession.id == run_id, RunningSession.user_id == user.id
    )).first()
    if run is None:
        raise ValueError("Running session not found for this account.")
    if (
        run.status != "completed" or run.source not in (None, "manual")
        or run.garmin_id is not None
    ):
        raise ValueError("Only completed, manually logged runs can be corrected.")
    old_distance = run.actual_km
    if (
        not isinstance(old_distance, Real) or isinstance(old_distance, bool)
        or not math.isfinite(float(old_distance)) or not 0 < float(old_distance) <= 100
    ):
        raise ValueError("The saved actual distance is outside supported correction bounds.")
    old_distance = float(old_distance)
    if new_distance == old_distance:
        return False

    with s.begin_nested():
        run.actual_km = new_distance
        s.add(RunCorrectionHistory(
            user_id=user.id,
            running_session_id=run.id,
            field_name="actual_km",
            corrected_at=datetime.now(timezone.utc),
            old_value=old_distance,
            new_value=new_distance,
        ))
        s.flush()
        recalc(s, user)
    return True


def _friendship(s, user_id: int, friend_id: int):
    return s.scalars(select(Friendship).where(
        Friendship.user_id == min(user_id, friend_id),
        Friendship.friend_id == max(user_id, friend_id),
    )).first()


def get_friendship(s, user_id, friend_id):
    return _friendship(s, user_id, friend_id)


def accepted_friends(s, user):
    rows = s.scalars(select(Friendship).where(
        Friendship.status == "accepted",
        (Friendship.user_id == user.id) | (Friendship.friend_id == user.id),
    )).all()
    friend_ids = {row.friend_id if row.user_id == user.id else row.user_id for row in rows}
    return list(s.scalars(select(User).where(User.id.in_(friend_ids)).order_by(User.name))) if friend_ids else []


def friend_requests(s, user):
    incoming = list(s.scalars(select(Friendship).where(
        Friendship.status == "pending", Friendship.requested_by != user.id,
        (Friendship.user_id == user.id) | (Friendship.friend_id == user.id),
    )))
    outgoing = list(s.scalars(select(Friendship).where(
        Friendship.status == "pending", Friendship.requested_by == user.id
    )))
    return incoming, outgoing


def send_friend_request(s, user, email: str) -> None:
    target = s.scalars(select(User).where(func.lower(User.email) == email.strip().lower())).first()
    if target is None:
        raise ValueError("No account was found for that email address.")
    if target.id == user.id:
        raise ValueError("You cannot send a friend request to yourself.")
    row = _friendship(s, user.id, target.id)
    if row and row.status == "accepted":
        raise ValueError("You are already connected.")
    if row and row.status == "pending":
        if row.requested_by != user.id:
            raise ValueError("That person has already sent you a request; accept it below.")
        raise ValueError("A friend request is already pending.")
    if row:
        row.requested_by, row.status = user.id, "pending"
    else:
        s.add(Friendship(user_id=min(user.id, target.id), friend_id=max(user.id, target.id),
                         requested_by=user.id, status="pending"))
    s.flush()


def respond_friend_request(s, user, friendship_id: int, accept: bool) -> None:
    row = s.get(Friendship, friendship_id)
    if (row is None or user.id not in (row.user_id, row.friend_id) or row.requested_by == user.id
            or row.status != "pending"):
        raise ValueError("Incoming friend request not found.")
    row.status = "accepted" if accept else "rejected"
    s.flush()


def update_permissions(s, user_id, friend_id, viewer, editor):
    row = _friendship(s, user_id, friend_id)
    if row:
        row.viewer = viewer
        row.editor = editor
        s.flush()


def assert_friend_access(s, actor, owner_id: int, edit: bool = False) -> None:
    if actor.id == owner_id:
        return
    row = _friendship(s, actor.id, owner_id)
    if row is None or row.status != "accepted":
        raise ValueError("Only an accepted friend can view or edit this plan.")
    if edit and not row.editor:
        raise ValueError("Only a friend with editor permissions can edit this plan.")
    if not row.viewer and not row.editor:
        raise ValueError("Only a friend with viewer or editor permissions can view this plan.")


def edit_shared_run(s, actor, owner, run_id: int, new_km: float, new_type: str) -> None:
    assert_friend_access(s, actor, owner.id, edit=True)
    run = s.get(RunningSession, run_id)
    if run is None or run.user_id != owner.id:
        raise ValueError("Running session not found for this plan.")
    if run.status != "planned" or run.date < date.today() or run.slot == "race":
        raise ValueError("Only future, unlogged, non-race sessions can be edited.")
    if new_km < 0 or new_km > run.planned_km * 1.1 + 1e-9:
        raise ValueError("Distance must be between 0 and 110% of the original planned distance.")
    if run.slot == "long" and new_km > get_settings(s, owner).peak_long_km:
        raise ValueError("Distance cannot exceed the plan's peak long-run target.")
    week = s.get(TrainingWeek, run.week_id) if run.week_id else None
    if week and week.phase in {"Taper", "Race Week"} and new_km > run.adjusted_km + 1e-9:
        raise ValueError("Distance cannot be increased during taper or race week.")
    if (run.adjusted_type or run.run_type) == "Event":
        raise ValueError("Event sessions cannot be changed.")
    week_runs = list(s.scalars(select(RunningSession).where(
        RunningSession.user_id == owner.id, RunningSession.week_id == run.week_id
    )))
    planned_week = sum(item.planned_km for item in week_runs)
    adjusted_week = sum(item.adjusted_km for item in week_runs)
    if adjusted_week - run.adjusted_km + new_km > planned_week * 1.1 + 1e-9:
        raise ValueError("The edit would exceed 110% of the planned weekly distance.")
    current_type = run.adjusted_type or run.run_type
    if new_type not in INTENSITY or rank(new_type) > rank(current_type):
        raise ValueError("Workout intensity cannot be increased.")
    s.add(AIAdjustment(
        user_id=owner.id, created_by_user_id=actor.id, session_date=run.date, kind="friend_edit",
        old_km=run.adjusted_km, new_km=new_km, old_type=current_type, new_type=new_type,
        reason=f"Edited by accepted friend {actor.name}", status="applied",
    ))
    s.flush()
    recalc(s, owner)


def update_shared_goal(s, actor, owner, race_date: date | None) -> None:
    assert_friend_access(s, actor, owner.id, edit=True)
    cfg = get_settings(s, owner)
    updated = PlanSettings.model_validate({**cfg.model_dump(), "race_date": race_date})
    save_settings(s, owner, updated)
    sync_plan(s, owner, updated)


def current_week_index(weeks, today: date) -> int:
    for i, w in enumerate(weeks):
        if w.week_start <= today < w.week_start + timedelta(days=7):
            return i
    return 0 if not weeks or today < weeks[0].week_start else len(weeks) - 1


def dashboard_summary(s, user, today: date | None = None) -> dict | None:
    today = today or date.today()
    cfg = get_settings(s, user)
    weeks, by = _week_runs(s, user)
    if not weeks:
        return None
    cur = weeks[current_week_index(weeks, today)]
    runs = [r for rs in by.values() for r in rs]
    nxt_long = min((r for r in runs if r.slot == "long" and r.date >= today and r.status == "planned"),
                   key=lambda r: r.date, default=None)
    nxt_event = s.scalars(select(Event).where(Event.user_id == user.id, Event.date >= today)
                          .order_by(Event.date)).first()
    factor = _current_factor(weeks, by, monday_of(today))
    if today < weeks[0].week_start:
        status = f"Not started: plan begins {weeks[0].week_start:%d %b %Y}"
    elif cur.phase in ("Taper", "Race Week"):
        status = "Taper: protect freshness"
    elif factor < 1:
        status = f"Adjusted: upcoming runs reduced {round((1 - factor) * 100)}%"
    else:
        status = "On track"
    return {"days_to_race": (cfg.race_date - today).days if cfg.race_date else None,
            "race_date": cfg.race_date, "week_no": cur.week_no,
            "total_weeks": len(weeks), "phase": cur.phase, "planned": cur.planned_km, "adjusted": cur.adjusted_km,
            "actual": cur.actual_km, "completion": cur.completion_pct, "next_long": nxt_long,
            "next_event": nxt_event, "status": status, "factor": factor}


def weekly_dataframe(s, user) -> pd.DataFrame:
    weeks, by = _week_runs(s, user)
    rows = []
    for w in weeks:
        rs = by.get(w.id, [])
        lg = next((r for r in rs if r.slot == "long"), None)
        rows.append({"week_no": w.week_no, "week_start": pd.Timestamp(w.week_start), "phase": w.phase,
                     "planned": w.planned_km, "adjusted": w.adjusted_km, "actual": w.actual_km,
                     "completion": w.completion_pct, "logged": any(r.status != "planned" for r in rs),
                     "long_planned": lg.planned_km if lg else None,
                     "long_actual": lg.actual_km if lg and lg.status == "completed" else None})
    return pd.DataFrame(rows, columns=["week_no", "week_start", "phase", "planned", "adjusted", "actual",
                                       "completion", "logged", "long_planned", "long_actual"])


def monthly_dataframe(s, user) -> pd.DataFrame:
    rows = [{"month": r.date.strftime("%Y-%m"), "planned": r.planned_km, "adjusted": r.adjusted_km,
             "actual": r.actual_km or 0} for r in all_runs(s, user)]
    if not rows:
        return pd.DataFrame(columns=["month", "planned", "adjusted", "actual"])
    return pd.DataFrame(rows).groupby("month", as_index=False).sum()


# ---------------------------------------------------------------- mutations
def log_run(s, user, run_id: int, status: str, actual_km: float | None, notes: str = "",
            today: date | None = None, overwrite: bool = False,
            manual_duration_min: float | None | _UnsetRunContext = _UNSET_RUN_CONTEXT,
            perceived_effort_rpe: int | None | _UnsetRunContext = _UNSET_RUN_CONTEXT) -> None:
    r = s.get(RunningSession, run_id)
    if r is None or r.user_id != user.id:
        raise ValueError("Running session not found for this account.")
    if r.garmin_id and r.source == "garmin" and not overwrite:
        raise ValueError("This run is linked to Garmin. Tick 'Overwrite manually' to change it.")
    if (
        r.status == "completed" and status == "completed"
        and r.source in (None, "manual") and r.garmin_id is None
    ):
        requested_actual = actual_km if actual_km is not None else r.adjusted_km
        if requested_actual != r.actual_km:
            raise ValueError("Use the run correction flow to change a completed run's actual distance.")
    if manual_duration_min is not _UNSET_RUN_CONTEXT and manual_duration_min is not None:
        if not isinstance(manual_duration_min, Real) or isinstance(manual_duration_min, bool):
            raise ValueError("Manual duration must be a positive finite number of minutes.")
        try:
            manual_duration_min = float(manual_duration_min)
        except OverflowError:
            raise ValueError("Manual duration must be a positive finite number of minutes.") from None
        if not math.isfinite(manual_duration_min) or manual_duration_min <= 0:
            raise ValueError("Manual duration must be a positive finite number of minutes.")
    if perceived_effort_rpe is not _UNSET_RUN_CONTEXT and perceived_effort_rpe is not None:
        if not isinstance(perceived_effort_rpe, Real) or isinstance(perceived_effort_rpe, bool):
            raise ValueError("Self-reported RPE must be a whole number from 1 to 10.")
        try:
            rpe_value = float(perceived_effort_rpe)
        except OverflowError:
            raise ValueError("Self-reported RPE must be a whole number from 1 to 10.") from None
        if not math.isfinite(rpe_value) or not rpe_value.is_integer() or not 1 <= rpe_value <= 10:
            raise ValueError("Self-reported RPE must be a whole number from 1 to 10.")
        perceived_effort_rpe = int(rpe_value)
    r.status = status
    r.notes = notes
    if status == "completed":
        r.actual_km = actual_km if actual_km is not None else r.adjusted_km
    elif status == "missed":
        r.actual_km = 0.0
    else:
        r.actual_km = None
    if manual_duration_min is not _UNSET_RUN_CONTEXT:
        r.manual_duration_min = manual_duration_min
    if perceived_effort_rpe is not _UNSET_RUN_CONTEXT:
        r.perceived_effort_rpe = perceived_effort_rpe
    r.source = "manual"
    if r.garmin_id and overwrite:
        r.manual_lock = True
    s.flush()
    recalc(s, user, today)


def unlock_run(s, user, run_id: int, today: date | None = None) -> None:
    """Hand the session back to Garmin; the next sync refreshes it."""
    run = s.get(RunningSession, run_id)
    if run is None or run.user_id != user.id:
        raise ValueError("Running session not found for this account.")
    run.manual_lock = False
    s.flush()
    recalc(s, user, today)


def log_strength(s, user, sid: int, completed: bool, notes: str = "") -> None:
    x = s.get(StrengthSession, sid)
    if x is None or x.user_id != user.id:
        raise ValueError("Strength session not found for this account.")
    x.completed, x.notes = completed, notes
    s.flush()


def add_event(s, user, name, event_type, d: date, distance_km: float, priority: str, notes: str = "",
              today: date | None = None) -> Event:
    e = Event(user_id=user.id, name=name, event_type=event_type, date=d, distance_km=distance_km,
              priority=priority, notes=notes)
    s.add(e)
    s.flush()
    recalc(s, user, today)
    return e


def delete_event(s, user, event_id: int, today: date | None = None) -> None:
    ev = s.get(Event, event_id)
    if not ev:
        return
    if ev.user_id != user.id:
        raise ValueError("Event not found for this account.")
    cfg = get_settings(s, user)
    if cfg.race_date == ev.date:
        save_settings(s, user, cfg.model_copy(update={"race_date": None}))
    s.delete(ev)
    s.flush()
    sync_plan(s, user, get_settings(s, user), today)


def reset_unlogged(s, user) -> None:
    for r in all_runs(s, user):
        if r.status == "planned" and r.actual_km is None:
            r.adjusted_km, r.adjusted_type = r.planned_km, r.run_type
    s.flush()


def add_note(s, user, d: date, category: str, text: str) -> None:
    s.add(Note(user_id=user.id, date=d, category=category, text=text))
    s.flush()


def delete_note(s, note_id: int, user=None) -> None:
    n = s.get(Note, note_id)
    if n is not None and user is not None and n.user_id != user.id:
        raise ValueError("Note not found for this account.")
    if n:
        s.delete(n)


def update_note(s, user, note_id: int, d: date, category: str, text: str) -> None:
    note = s.get(Note, note_id)
    if note is None or note.user_id != user.id:
        raise ValueError("Note not found for this account.")
    if category not in NOTE_CATEGORIES:
        raise ValueError("Invalid note category.")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Note text cannot be blank.")
    note.date = d
    note.category = category
    note.text = text.strip()
    s.flush()


def _week_days(d: date) -> list[date]:
    m = monday_of(d)
    return [m + timedelta(days=i) for i in range(7)]


def set_strength_status(s, user, sid: int, status: str) -> None:
    if status not in ("planned", "completed", "missed"):
        raise ValueError("Invalid status")
    session = s.get(StrengthSession, sid)
    if session is None or session.user_id != user.id:
        raise ValueError("Strength session not found for this account.")
    session.status = status
    s.flush()


def free_days(s, kind: str, current: date) -> list[date]:
    """Days in the same week this session may move to."""
    model = StrengthSession if kind == "strength" else RunningSession
    taken = {x.date for x in s.scalars(select(model))}
    return [d for d in _week_days(current) if d != current and d not in taken]


def move_session(s, user, kind: str, sid: int, new_date: date) -> None:
    model = StrengthSession if kind == "strength" else RunningSession
    obj = s.get(model, sid)
    if monday_of(new_date) != monday_of(obj.date):
        raise ValueError("Sessions can only move within the same week.")
    if new_date not in free_days(s, kind, obj.date):
        raise ValueError("That day already has a " + ("strength session." if kind == "strength"
                                                      else "run. Two runs in one day aren't allowed."))
    obj.date = new_date
    if kind == "strength" and obj.status == "missed":
        obj.status = "planned"
    s.flush()
    if kind == "run":
        recalc(s, user)