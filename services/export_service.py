import io
import json
from datetime import date, datetime

import pandas as pd
from sqlalchemy import DateTime, Date, inspect, select

from models.orm import (
    AIAdjustment, Event, Note, RunningSession, Setting, StrengthLog, StrengthSession, TrainingWeek, User,
)

TABLES = {"users": User, "settings": Setting, "training_weeks": TrainingWeek, "running_sessions": RunningSession,
          "strength_sessions": StrengthSession, "strength_logs": StrengthLog, "events": Event, "notes": Note,
          "ai_adjustments": AIAdjustment}


def _ser(v):
    return v.isoformat() if isinstance(v, (date, datetime)) else v


def dump_all(s, user=None) -> dict:
    tables = {}
    for name, model in TABLES.items():
        query = select(model)
        if user is not None:
            query = query.where(model.id == user.id) if model is User else query.where(model.user_id == user.id)
        if model is Setting:
            query = query.where(Setting.key.notin_(("garmin_email_enc", "garmin_password_enc")))
        rows = []
        for obj in s.scalars(query):
            values = {}
            for column in inspect(model).column_attrs:
                if user is not None and model is User and column.key in {"auth_uid", "email"}:
                    continue
                if user is not None and model is AIAdjustment and column.key == "created_by_user_id":
                    continue
                values[column.key] = _ser(getattr(obj, column.key))
            rows.append(values)
        tables[name] = rows
    return tables


def to_json(s, user=None) -> str:
    return json.dumps({"app": "Adam's Marathon Planner", "version": 1, "exported_at": datetime.now().isoformat(),
                       "tables": dump_all(s, user)}, indent=2)


def _parse(col, v):
    if v is None:
        return None
    if isinstance(col.type, DateTime):
        return datetime.fromisoformat(v)
    if isinstance(col.type, Date):
        return date.fromisoformat(v)
    return v


def restore_json(s, text: str, user=None) -> dict[str, int]:
    data = json.loads(text)
    tables = data.get("tables") if isinstance(data, dict) else None
    legacy_tables = set(TABLES) - {"strength_logs"}
    if not isinstance(tables, dict) or set(tables) not in (legacy_tables, set(TABLES)):
        raise ValueError("Invalid backup file: tables do not match this application.")
    if "strength_logs" not in tables:
        tables["strength_logs"] = []
    if user is not None:
        counts = {"users": 1}
        models = [model for name, model in TABLES.items() if name != "users"]
        for model in reversed(models):
            query = select(model).where(model.user_id == user.id)
            if model is Setting:
                query = query.where(Setting.key.notin_(("garmin_email_enc", "garmin_password_enc")))
            rows = s.scalars(query).all()
            for obj in rows:
                s.delete(obj)
        s.flush()
        week_ids = {}
        for name, model in TABLES.items():
            if model is User:
                continue
            restored_count = 0
            for row in tables[name]:
                if model is Setting and row.get("key") in {"garmin_email_enc", "garmin_password_enc"}:
                    continue
                values = {
                    k: _parse(model.__table__.columns[k], v) for k, v in row.items()
                    if k in model.__table__.columns
                    and k not in {"id", "user_id"}
                    and not (user is not None and model is AIAdjustment and k == "created_by_user_id")
                }
                values["user_id"] = user.id
                if model is RunningSession or model is StrengthSession:
                    old_week_id = row.get("week_id")
                    values["week_id"] = week_ids.get(old_week_id) if old_week_id is not None else None
                obj = model(**values)
                s.add(obj)
                s.flush()
                if model is TrainingWeek:
                    week_ids[row.get("id")] = obj.id
                restored_count += 1
            counts[name] = restored_count
        return counts

    for model in reversed(list(TABLES.values())):
        query = select(model)
        if model is Setting:
            query = query.where(Setting.key.notin_(("garmin_email_enc", "garmin_password_enc")))
        for o in s.scalars(query).all():
            s.delete(o)
    s.flush()
    counts = {}
    for name, model in TABLES.items():
        cols = model.__table__.columns
        restored_count = 0
        for row in tables[name]:
            if model is Setting and row.get("key") in {"garmin_email_enc", "garmin_password_enc"}:
                continue
            s.add(model(**{k: _parse(cols[k], v) for k, v in row.items()}))
            restored_count += 1
        counts[name] = restored_count
    s.flush()
    return counts


def table_frames(s, user=None) -> dict[str, pd.DataFrame]:
    return {name: pd.DataFrame(rows) for name, rows in dump_all(s, user).items()}


def table_csv(s, name: str, user=None) -> str:
    return table_frames(s, user)[name].to_csv(index=False)


def to_excel(s, user=None) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        for name, df in table_frames(s, user).items():
            df.to_excel(xl, sheet_name=name[:31], index=False)
    return buf.getvalue()