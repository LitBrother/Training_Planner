import math
from datetime import date, datetime
from numbers import Real

import pandas as pd
from sqlalchemy import select

from models.orm import StrengthLog, StrengthSetDetail

REP_RANGE = (5, 8)   # change per exercise if you like
STEP_KG = 2.5


def _validated_log_date(d: date) -> date:
    if not isinstance(d, date) or isinstance(d, datetime):
        raise ValueError("Date must be a valid date.")
    return d


def _validated_log_values(exercise: str, weight: float, sets: int, reps: int):
    if not isinstance(exercise, str) or not exercise.strip():
        raise ValueError("Exercise must not be blank.")
    if (not isinstance(weight, Real) or isinstance(weight, bool)
            or not math.isfinite(weight) or not 0 <= weight <= 500):
        raise ValueError("Weight must be between 0 and 500 kg.")
    if not isinstance(sets, int) or isinstance(sets, bool) or not 1 <= sets <= 10:
        raise ValueError("Sets must be between 1 and 10.")
    if not isinstance(reps, int) or isinstance(reps, bool) or not 1 <= reps <= 50:
        raise ValueError("Reps must be between 1 and 50.")
    return exercise.strip().title(), float(weight), sets, reps


def _validated_set_details(set_details: list[dict]) -> list[tuple[float, int]]:
    if not isinstance(set_details, list) or not 1 <= len(set_details) <= 10:
        raise ValueError("A detailed lift must contain between 1 and 10 sets.")
    validated = []
    for number, row in enumerate(set_details, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Set {number}: enter a weight and reps.")
        weight = row.get("weight_kg")
        reps = row.get("reps")
        if (not isinstance(weight, Real) or isinstance(weight, bool)
                or not math.isfinite(weight) or not 0 <= weight <= 500):
            raise ValueError(f"Set {number}: weight must be between 0 and 500 kg.")
        tenths = round(weight * 10)
        if not math.isclose(weight * 10, tenths, rel_tol=0, abs_tol=1e-7):
            raise ValueError(f"Set {number}: weight must use 0.1 kg increments.")
        if not isinstance(reps, int) or isinstance(reps, bool) or not 1 <= reps <= 50:
            raise ValueError(f"Set {number}: reps must be between 1 and 50.")
        validated.append((tenths / 10, reps))
    return validated


def _owned_log(s, user, log_id: int) -> StrengthLog:
    log = s.scalars(select(StrengthLog).where(
        StrengthLog.id == log_id,
        StrengthLog.user_id == user.id,
    )).first()
    if log is None:
        raise ValueError("Strength log not found for this account.")
    return log


def est_1rm(weight: float, reps: int) -> float:
    return round(weight * (1 + reps / 30), 1)  # Epley formula


def add_log(s, user, d: date, exercise: str, weight: float, sets: int, reps: int) -> None:
    d = _validated_log_date(d)
    exercise, weight, sets, reps = _validated_log_values(exercise, weight, sets, reps)
    s.add(StrengthLog(user_id=user.id, date=d, exercise=exercise,
                      weight_kg=weight, sets=sets, reps=reps))
    s.flush()


def update_log(
    s, user, log_id: int, d: date, exercise: str, weight: float, sets: int, reps: int
) -> None:
    d = _validated_log_date(d)
    log = _owned_log(s, user, log_id)
    if s.scalar(select(StrengthSetDetail.id).where(
        StrengthSetDetail.user_id == user.id,
        StrengthSetDetail.strength_log_id == log.id,
    ).limit(1)) is not None:
        raise ValueError("Detailed logs must be edited using their per-set values.")
    exercise, weight, sets, reps = _validated_log_values(exercise, weight, sets, reps)
    log.date = d
    log.exercise = exercise
    log.weight_kg = weight
    log.sets = sets
    log.reps = reps
    s.flush()


def delete_log(s, user, log_id: int) -> None:
    log = _owned_log(s, user, log_id)
    s.query(StrengthSetDetail).filter(
        StrengthSetDetail.user_id == user.id,
        StrengthSetDetail.strength_log_id == log.id,
    ).delete(synchronize_session=False)
    s.delete(log)
    s.flush()


def list_logs(s, user) -> list[StrengthLog]:
    return list(s.scalars(select(StrengthLog).where(
        StrengthLog.user_id == user.id
    ).order_by(StrengthLog.date.desc(), StrengthLog.id.desc())))


def _set_summary(validated_sets: list[tuple[float, int]]) -> tuple[float, int]:
    highest_weight = max(weight for weight, _ in validated_sets)
    highest_weight_reps = max(
        reps for weight, reps in validated_sets if weight == highest_weight
    )
    return highest_weight, highest_weight_reps


def add_detailed_log(
    s, user, d: date, exercise: str, set_details: list[dict]
) -> int:
    d = _validated_log_date(d)
    validated_sets = _validated_set_details(set_details)
    highest_weight, highest_weight_reps = _set_summary(validated_sets)
    exercise, _, _, _ = _validated_log_values(
        exercise, highest_weight, len(validated_sets), highest_weight_reps
    )
    log = StrengthLog(
        user_id=user.id,
        date=d,
        exercise=exercise,
        weight_kg=highest_weight,
        sets=len(validated_sets),
        reps=highest_weight_reps,
    )
    s.add(log)
    s.flush()
    for number, (weight, reps) in enumerate(validated_sets, start=1):
        s.add(StrengthSetDetail(
            user_id=user.id,
            strength_log_id=log.id,
            set_number=number,
            weight_kg=weight,
            reps=reps,
        ))
    s.flush()
    return log.id


def update_detailed_log(
    s, user, log_id: int, d: date, exercise: str, set_details: list[dict]
) -> None:
    d = _validated_log_date(d)
    log = _owned_log(s, user, log_id)
    existing = s.scalars(select(StrengthSetDetail).where(
        StrengthSetDetail.user_id == user.id,
        StrengthSetDetail.strength_log_id == log.id,
    )).all()
    if not existing:
        raise ValueError("Detailed strength log not found for this account.")
    validated_sets = _validated_set_details(set_details)
    highest_weight, highest_weight_reps = _set_summary(validated_sets)
    exercise, _, _, _ = _validated_log_values(
        exercise, highest_weight, len(validated_sets), highest_weight_reps
    )
    log.date = d
    log.exercise = exercise
    log.weight_kg = highest_weight
    log.sets = len(validated_sets)
    log.reps = highest_weight_reps
    for row in existing:
        s.delete(row)
    s.flush()
    for number, (weight, reps) in enumerate(validated_sets, start=1):
        s.add(StrengthSetDetail(
            user_id=user.id,
            strength_log_id=log.id,
            set_number=number,
            weight_kg=weight,
            reps=reps,
        ))
    s.flush()


def list_detailed_logs(s, user) -> list[tuple[StrengthLog, list[StrengthSetDetail]]]:
    logs = s.scalars(select(StrengthLog).where(
        StrengthLog.user_id == user.id
    ).order_by(StrengthLog.date.desc(), StrengthLog.id.desc())).all()
    details = s.scalars(select(StrengthSetDetail).where(
        StrengthSetDetail.user_id == user.id
    ).order_by(StrengthSetDetail.strength_log_id, StrengthSetDetail.set_number)).all()
    grouped: dict[int, list[StrengthSetDetail]] = {}
    for detail in details:
        grouped.setdefault(detail.strength_log_id, []).append(detail)
    return [(log, grouped[log.id]) for log in logs if log.id in grouped]


def log_frame(s, user) -> pd.DataFrame:
    rows = s.scalars(select(StrengthLog).where(StrengthLog.user_id == user.id)
                     .order_by(StrengthLog.date, StrengthLog.id)).all()
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