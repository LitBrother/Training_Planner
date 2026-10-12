from datetime import date
from utils.dates import monday_of


def match_runs(runs: list[dict], sessions: list[dict], max_late: int = 3, max_early: int = 1) -> dict[str, int]:
    """runs: {id, date, distance_km}; sessions: {id, date, km} (unlinked only).
    Returns {garmin_id: session_id}. Greedy best-score-first, one run per session."""
    cands = []
    for r in runs:
        for s in sessions:
            if not s["km"]:
                continue
            days = (r["date"] - s["date"]).days   # positive = done late
            if days > max_late or days < -max_early:
                continue
            ratio = abs(r["distance_km"] - s["km"]) / s["km"]
            if ratio > 0.5:
                continue
            score = ratio + 0.15 * abs(days) + (0.1 if days < 0 else 0)
            cands.append((score, r["id"], s["id"]))
    used_r, used_s, out = set(), set(), {}
    for _, rid, sid in sorted(cands):
        if rid in used_r or sid in used_s:
            continue
        out[rid] = sid
        used_r.add(rid)
        used_s.add(sid)
    return out


def match_long_runs(runs: list[dict], sessions: list[dict], min_ratio: float = 0.75) -> dict[str, int]:
    """sessions need: id, date, km, slot. Returns {garmin_id: session_id}."""
    out: dict[str, int] = {}
    weeks = {monday_of(r["date"]) for r in runs}
    for wk in weeks:
        longs = [x for x in sessions if x.get("slot") == "long" and monday_of(x["date"]) == wk and x["km"]]
        week_runs = [r for r in runs if monday_of(r["date"]) == wk]
        if not longs or not week_runs:
            continue
        top = max(week_runs, key=lambda r: r["distance_km"])
        sess = longs[0]
        if top["distance_km"] >= min_ratio * sess["km"]:
            out[top["id"]] = sess["id"]
    return out