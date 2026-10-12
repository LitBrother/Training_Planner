from datetime import date

import pandas as pd
from sqlalchemy import select

from models.orm import StrengthLog

REP_RANGE = (5, 8)   # change per exercise if you like
STEP_KG = 2.5


def est_1rm(weight: float, reps: int) -> float:
    return round(weight * (1 + reps / 30), 1)  # Epley formula


def add_log(s, user, d: date, exercise: str, weight: float, sets: int, reps: int) -> None:
    s.add(StrengthLog(user_id=user.id, date=d, exercise=exercise.strip().title(),
                      weight_kg=weight, sets=sets, reps=reps))
    s.flush()


def log_frame(s, user) -> pd.DataFrame:
    rows = s.scalars(select(StrengthLog).where(StrengthLog.user_id == user.id)
                     .order_by(StrengthLog.date)).all()
    df = pd.DataFrame([{"date": r.date, "exercise": r.exercise, "weight_kg": r.weight_kg,
                        "sets": r.sets, "reps": r.reps} for r in rows])
    if not df.empty:
        df["est_1rm"] = [est_1rm(w, r) for w, r in zip(df["weight_kg"], df["reps"])]
    return df


def suggest_next(df: pd.DataFrame, exercise: str, hold: bool = False) -> str:
    d = df[df["exercise"] == exercise]
    if d.empty:
        return "No history yet."
    last = d.iloc[-1]
    lo, hi = REP_RANGE
    if hold:
        return f"Training load is high. Repeat {last.weight_kg} kg x {last.reps}."
    if last.reps >= hi:
        return f"Add load: {last.weight_kg + STEP_KG} kg x {lo} reps per set."
    return f"Stay at {last.weight_kg} kg and aim for {last.reps + 1} reps per set."