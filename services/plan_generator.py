from datetime import date, timedelta

from utils.dates import monday_of

PHASE_GOALS = {
    "Base Development": "Consistency, durability, aerobic foundation, easy running habit.",
    "Aerobic Build": "Threshold development, aerobic capacity, longer long runs.",
    "Marathon Build": "Marathon-specific preparation, fueling practice, long-run development.",
    "Peak": "Race-specific fitness, pacing practice, final endurance development.",
    "Taper": "Maintain intensity, reduce volume, reduce fatigue.",
    "Race Week": "Minimal fatigue, short easy runs, no heavy leg training.",
}

TUESDAY = {
    "Base Development": [("Easy + Strides", "Easy run with strides"), ("Fartlek", "Short fartlek"), ("Easy", "Easy run")],
    "Aerobic Build": [("Threshold", "Threshold intervals"), ("Steady", "Tempo run"), ("Intervals", "Aerobic intervals")],
    "Marathon Build": [("Threshold", "Threshold session"), ("Marathon Pace", "Marathon pace intervals")],
    "Peak": [("Marathon Pace", "Marathon pace intervals"), ("Threshold", "Threshold session")],
    "Taper": [("Threshold", "Threshold (reduced volume)"), ("Marathon Pace", "Marathon pace (reduced volume)")],
}


def _r(x: float) -> float:
    return round(x * 2) / 2


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _quality_km(long_km: float) -> float:
    return float(_clip(round(0.38 * long_km), 6, 14))


def _weekend_km(long_km: float) -> float:
    return float(_clip(_r(0.3 * long_km), 5, 12))


def split_build(build: int) -> tuple[int, int, int, int]:
    """Return (base, aerobic, marathon_build, peak) week counts summing to build."""
    if build <= 0:
        return 0, 0, 0, 0
    peak = max(1, round(build * 0.15))
    mb = round(build * 0.25)
    ab = round(build * 0.30)
    over = peak + mb + ab - build
    while over > 0:
        if mb > 0:
            mb -= 1
        elif ab > 0:
            ab -= 1
        else:
            peak -= 1
        over -= 1
    return build - peak - mb - ab, ab, mb, peak


def build_phase_list(n_weeks: int, taper: int) -> list[str]:
    taper = max(0, min(taper, n_weeks - 1))
    build = n_weeks - 1 - taper
    base, ab, mb, peak = split_build(build)
    return (["Base Development"] * base + ["Aerobic Build"] * ab + ["Marathon Build"] * mb
            + ["Peak"] * peak + ["Taper"] * taper + ["Race Week"])


def long_run_curve(b: int, start: float, peak: float) -> list[tuple[float, bool]]:
    """Gradual long-run progression (max +3 km/week) with a recovery week every 4th week."""
    out: list[tuple[float, bool]] = []
    last = start
    for i in range(b):
        t = start + (peak - start) * (i / (b - 1) if b > 1 else 1.0)
        if i > 0:
            t = min(t, last + 3)
        t = min(_r(t), peak)
        if i % 4 == 3 and i < b - 1:
            out.append((_r(t * 0.75), True))
        else:
            out.append((t, False))
            last = t
    return out


def _run(ws: date, weekday: int, slot: str, run_type: str, variant: str, name: str, km: float) -> dict:
    return {"date": ws + timedelta(days=weekday), "weekday": weekday, "slot": slot, "run_type": run_type,
            "variant": variant, "name": name, "km": float(km)}


def _build_runs(ws, phase, k, rec, long_km, q, w, wk_day) -> list[dict]:
    if rec:
        tt, tn = "Easy + Strides", "Easy run with strides"
    else:
        tt, tn = TUESDAY[phase][k % len(TUESDAY[phase])]
    mp = (not rec) and (phase == "Peak" or (phase == "Marathon Build" and k % 2 == 1))
    wtype = "Recovery" if rec else "Easy"
    return [
        _run(ws, 1, "quality", tt, "", tn, q),
        _run(ws, 4, "long", "Long Run", "MP finish" if mp else "Easy",
             "Long run (MP finish + fueling)" if mp else "Long run (easy)", long_km),
        _run(ws, wk_day, "weekend", wtype, "", f"Weekend {wtype.lower()} run / hike / event", w),
    ]


def _race_week_runs(ws: date, race_date: date) -> list[dict]:
    runs = [
        _run(ws, 1, "quality", "Easy + Strides", "", "Race-week sharpener", 6),
        _run(ws, 4, "long", "Shakeout", "", "Pre-race shakeout", 4),
        _run(ws, 5, "weekend", "Shakeout", "", "Short shakeout", 3),
    ]
    runs = [r for r in runs if r["date"] < race_date]
    runs.append(_run(ws, race_date.weekday(), "race", "Race", "", "Marathon race day", 42.2))
    return runs


def _strength(ws: date, phase: str, long_km: float, race_date: date) -> list[dict]:
    if phase == "Race Week":
        level = "none"
    elif phase == "Taper":
        level = "minimal"
    elif long_km > 28:
        level = "reduced"
    else:
        level = "normal"
    core = 10 if phase == "Race Week" else 12
    items = [(0, "Push", core, "normal"), (2, "Pull", 10, "normal"), (3, "Legs", 0, level)]
    return [{"date": ws + timedelta(days=d), "weekday": d, "kind": kind, "core_minutes": cm, "volume_level": lv}
            for d, kind, cm, lv in items if ws + timedelta(days=d) < race_date]


def generate_plan(plan_start: date, race_date: date, peak_long_km: float = 32.0, start_long_km: float = 14.0,
                  taper_weeks: int = 3, weekend_day: str = "Saturday") -> list[dict]:
    first = monday_of(plan_start)
    n = (monday_of(race_date) - first).days // 7 + 1
    if n < 2:
        raise ValueError("Race date must be at least one week after the plan start.")
    phases = build_phase_list(n, taper_weeks)
    b = sum(1 for p in phases if p not in ("Taper", "Race Week"))
    curve = long_run_curve(b, start_long_km, peak_long_km)
    peak_l = curve[-1][0] if curve else peak_long_km
    n_taper = phases.count("Taper")
    wk_day = 6 if weekend_day == "Sunday" else 5
    weeks, counters = [], {}
    for i, phase in enumerate(phases):
        ws = first + timedelta(weeks=i)
        k = counters.get(phase, 0)
        counters[phase] = k + 1
        rec = False
        if i < b:
            long_km, rec = curve[i]
            q, w = _quality_km(long_km), _weekend_km(long_km)
            if rec:
                q, w = _r(q * 0.8), _r(w * 0.8)
        elif phase == "Taper":
            f = 1 - 0.6 * (k + 1) / n_taper
            long_km = _r(peak_l * f)
            q = max(5.0, _r(_quality_km(peak_l) * f))
            w = max(4.0, _r(_weekend_km(peak_l) * f))
        else:
            long_km = q = w = 0.0
        runs = _race_week_runs(ws, race_date) if phase == "Race Week" else \
            _build_runs(ws, phase, k, rec, long_km, q, w, wk_day)
        weeks.append({"week_no": i + 1, "week_start": ws, "phase": phase, "is_recovery": rec,
                      "runs": runs, "strength": _strength(ws, phase, long_km, race_date)})
    return weeks