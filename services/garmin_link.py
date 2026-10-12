from sqlalchemy import select

from models.orm import RunningSession
from services import plan_service as ps
from services.garmin_match import match_long_runs, match_runs


def link(s, session_id: int, run: dict) -> None:
    sess = s.get(RunningSession, session_id)
    if sess.manual_lock:
        raise ValueError("That session was set manually. Unlock it first.")
    sess.garmin_id = run["id"]
    sess.actual_km = run["distance_km"]
    sess.status = "completed"
    sess.source = "garmin"


def auto_link(s, user, runs: list[dict]) -> int:
    sessions = list(s.scalars(select(RunningSession).where(RunningSession.user_id == user.id)))
    by_id = {r["id"]: r for r in runs}
    # refresh already linked, unlocked sessions
    for x in sessions:
        if x.garmin_id in by_id and not x.manual_lock:
            x.actual_km = by_id[x.garmin_id]["distance_km"]
            x.status = "completed"
    linked = {x.garmin_id for x in sessions if x.garmin_id}
    free_runs = [r for r in runs if r["id"] not in linked]
    open_sessions = [{"id": x.id, "date": x.date, "km": x.adjusted_km, "slot": x.slot}
                     for x in sessions if not x.garmin_id and not x.manual_lock and x.adjusted_km]
    long_pairs = match_long_runs(free_runs, open_sessions)
    for rid, sid in long_pairs.items():
        link(s, sid, by_id[rid])
    free_runs = [r for r in free_runs if r["id"] not in long_pairs]
    open_sessions = [x for x in open_sessions if x["id"] not in long_pairs.values()]
    pairs = match_runs(free_runs, open_sessions)
    for rid, sid in pairs.items():
        link(s, sid, by_id[rid])
    ps.recalc(s, user)
    return len(pairs) + len(long_pairs)