from sqlalchemy import select

from models.orm import RunningSession
from services import plan_service as ps
from services.garmin_match import match_long_runs, match_runs


def link(s, user, session_id: int, run: dict) -> None:
    sess = s.get(RunningSession, session_id)
    if sess is None or sess.user_id != user.id:
        raise ValueError("Running session not found for this account.")
    if sess.manual_lock:
        raise ValueError("That session was set manually. Unlock it first.")
    
    # Idempotent re-link: if already linked to this run, do nothing.
    if sess.garmin_id == run["id"]:
        return
        
    # Prevent linking to an already-linked session (that wasn't this run)
    if sess.garmin_id is not None:
         raise ValueError("That session is already linked to a different Garmin run.")

    sess.garmin_id = run["id"]
    sess.actual_km = run["distance_km"]
    sess.status = "completed"
    sess.source = "garmin"


def unlink(s, user, session_id: int) -> None:
    """Remove a Garmin match from a session. Raises ValueError if the session
    is not found, doesn't belong to this user, has no Garmin link, or is
    manually locked."""
    sess = s.get(RunningSession, session_id)
    if sess is None or sess.user_id != user.id:
        raise ValueError("Running session not found for this account.")
    if sess.manual_lock:
        raise ValueError("That session was set manually. Unlock it first.")
    if not sess.garmin_id:
        raise ValueError("That session has no Garmin match to remove.")
    sess.garmin_id = None
    sess.actual_km = None
    sess.status = "planned"
    sess.source = None


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
        link(s, user, sid, by_id[rid])
    free_runs = [r for r in free_runs if r["id"] not in long_pairs]
    open_sessions = [x for x in open_sessions if x["id"] not in long_pairs.values()]
    pairs = match_runs(free_runs, open_sessions)
    for rid, sid in pairs.items():
        link(s, user, sid, by_id[rid])
    ps.recalc(s, user)
    return len(pairs) + len(long_pairs)