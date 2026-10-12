from datetime import date, timedelta

DEFAULT_MAX_HR = 190  # set your real max HR; 220 - age is only a rough estimate


def _km(runs: list[dict], start: date, end: date) -> float:
    return sum(r["distance_km"] for r in runs if start <= r["date"] <= end)


def analyze(runs: list[dict], today: date, max_hr: int = DEFAULT_MAX_HR) -> dict:
    acute = _km(runs, today - timedelta(days=6), today)
    prev_week = _km(runs, today - timedelta(days=13), today - timedelta(days=7))
    chronic = _km(runs, today - timedelta(days=27), today) / 4
    acwr = round(acute / chronic, 2) if chronic > 0 else None

    flags: list[str] = []
    reduce_pct = 0
    if acwr and acwr > 1.5:
        reduce_pct = 20
        flags.append(f"Load spike (ACWR {acwr}): last 7 days far above your 4-week average.")
    elif acwr and acwr > 1.3:
        reduce_pct = 10
        flags.append(f"Load rising fast (ACWR {acwr}). Trim volume to stay safe.")
    if prev_week > 0 and acute > prev_week * 1.10:
        flags.append(f"Weekly mileage up {round((acute / prev_week - 1) * 100)}% (guideline is about 10%).")
        reduce_pct = max(reduce_pct, 10)

    hard_easy = [
        r for r in runs[-10:]
        if r.get("avg_hr") and r["avg_hr"] > 0.80 * max_hr and r["distance_km"] >= 5
    ]
    if len(hard_easy) >= 3:
        flags.append("Several recent runs had a high average HR. Easy runs are probably too fast.")

    if not any(r["date"] >= today - timedelta(days=7) for r in runs):
        flags.append("No runs in the last 7 days. Don't jump back to full volume.")

    return {
        "acute_km": round(acute, 1), "prev_week_km": round(prev_week, 1),
        "chronic_weekly_km": round(chronic, 1), "acwr": acwr,
        "reduce_pct": reduce_pct, "flags": flags, "easy_too_hard": len(hard_easy) >= 3,
    }


NOTES = {
    "long": "Slow down. Run 45-90 s/km slower than marathon pace, HR in zone 2. Practise fuelling: 30-60 g carbs/hour.",
    "easy": "Conversational pace. If you can't speak in full sentences, slow down.",
    "recovery": "Very easy and short. The goal is to absorb training, not add to it.",
    "tempo": "Comfortably hard, steady effort. Warm up 10-15 min and keep the pace even.",
    "interval": "Controlled hard reps with full recovery. Stop if form falls apart.",
    "race": "Start slower than goal pace for the first 5 km.",
}


def run_note(session_type: str, analysis: dict) -> str:
    t = (session_type or "").lower()
    key = next((k for k in NOTES if k in t), "easy")
    note = NOTES[key]
    if analysis.get("easy_too_hard") and key in ("easy", "long", "recovery"):
        note += " Your recent HR was high, so this run should feel easier than you think."
    if analysis.get("reduce_pct") and key in ("tempo", "interval"):
        note += " Load is high, so cut a rep or two or run it at steady effort."
    return note


def compact_runs(runs: list[dict], today: date, days: int = 28) -> list[dict]:
    """Small payload for the AI: last N days with pace in min/km."""
    out = []
    for r in sorted(runs, key=lambda x: x["date"]):
        if r["date"] < today - timedelta(days=days):
            continue
        dur, km = r.get("duration_min"), r["distance_km"]
        out.append({"date": r["date"].isoformat(), "km": km, "min": dur,
                    "pace": round(dur / km, 2) if dur and km else None, "hr": r.get("avg_hr")})
    return out


def trends(runs: list[dict], today: date) -> dict:
    """Weekly km for the last 6 weeks, longest recent run, and days since the last run."""
    weekly = [round(_km(runs, today - timedelta(days=7 * i + 6), today - timedelta(days=7 * i)), 1)
              for i in range(6)][::-1]
    recent = [r for r in runs if r["date"] >= today - timedelta(days=28)]
    longest = max((r["distance_km"] for r in recent), default=0)
    last = max((r["date"] for r in runs), default=None)
    return {"weekly_km_6w": weekly, "longest_run_km": round(longest, 1),
            "days_since_run": (today - last).days if last else None}


def proposals_from_load(analysis: dict, upcoming: dict[date, dict]) -> list[dict]:
    """Reduce-only proposals in the same format the AI validator accepts."""
    pct = analysis.get("reduce_pct", 0)
    if not pct:
        return []
    out = []
    for d, sess in sorted(upcoming.items())[:7]:
        km = sess.get("adjusted_km")
        if not km or sess.get("slot") == "race":
            continue
        out.append({
            "session_date": d.isoformat(),
            "kind": "reduce_volume",
            "new_km": round(km * (1 - pct / 100), 1),
            "new_type": None,
            "reason": "; ".join(analysis["flags"]) or "Load management",
        })
    return out