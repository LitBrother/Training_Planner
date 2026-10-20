"""Pure baseline plan rules; separately validated AI changes may adjust volume."""

INTENSITY = {
    "Rest": 0, "Recovery": 1, "Shakeout": 1, "Easy": 2, "Easy + Strides": 2, "Long Run": 3,
    "Steady": 4, "Marathon Pace": 5, "Fartlek": 6, "Threshold": 7, "Intervals": 8, "Event": 9, "Race": 9,
}


def rank(run_type: str | None) -> int:
    return INTENSITY.get(run_type or "", 99)


def completion_pct(planned: float, actual: float) -> float:
    if planned <= 0:
        return 0.0
    return round(min(100.0, actual / planned * 100), 1)


def reduction_factor(pct: float) -> float:
    if pct >= 90:
        return 1.0
    if pct >= 75:
        return 0.95
    return 0.9


def adjust_km(km: float, factor: float) -> float:
    return round(km * factor, 1)


def apply_event_week(rows: list[dict], event_km: float) -> list[dict]:
    """Event week keeps the same structure: Tuesday reduced, Friday reduced/shakeout, weekend = event."""
    out = [dict(r) for r in rows]
    for r in out:
        if r["slot"] == "race":
            continue
        if r["slot"] == "quality":
            r["km"] = round(r["km"] * (0.6 if event_km >= 21 else 0.7), 1)
        elif r["slot"] == "long":
            if event_km >= 15:
                r["km"] = min(r["km"], 5.0)
                r["run_type"] = "Shakeout"
            else:
                r["km"] = round(r["km"] * 0.8, 1)
        elif r["slot"] == "weekend":
            r["km"] = float(event_km)
            r["run_type"] = "Event"
    return out