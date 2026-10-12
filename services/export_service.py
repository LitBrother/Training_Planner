import io
import json
from datetime import date, datetime

import pandas as pd
from sqlalchemy import DateTime, Date, inspect, select

from models.orm import (AIAdjustment, Event, Note, RunningSession, Setting, StrengthSession, TrainingWeek, User)

TABLES = {"users": User, "settings": Setting, "training_weeks": TrainingWeek, "running_sessions": RunningSession,
          "strength_sessions": StrengthSession, "events": Event, "notes": Note, "ai_adjustments": AIAdjustment}


def _ser(v):
    return v.isoformat() if isinstance(v, (date, datetime)) else v


def dump_all(s) -> dict:
    return {name: [{c.key: _ser(getattr(o, c.key)) for c in inspect(model).column_attrs}
                   for o in s.scalars(select(model))] for name, model in TABLES.items()}


def to_json(s) -> str:
    return json.dumps({"app": "Adam's Marathon Planner", "version": 1, "exported_at": datetime.now().isoformat(),
                       "tables": dump_all(s)}, indent=2)


def _parse(col, v):
    if v is None:
        return None
    if isinstance(col.type, DateTime):
        return datetime.fromisoformat(v)
    if isinstance(col.type, Date):
        return date.fromisoformat(v)
    return v


def restore_json(s, text: str) -> dict[str, int]:
    data = json.loads(text)
    tables = data.get("tables") if isinstance(data, dict) else None
    if not isinstance(tables, dict) or set(tables) != set(TABLES):
        raise ValueError("Invalid backup file: tables do not match this application.")
    for model in reversed(list(TABLES.values())):
        for o in s.scalars(select(model)).all():
            s.delete(o)
    s.flush()
    counts = {}
    for name, model in TABLES.items():
        cols = model.__table__.columns
        for row in tables[name]:
            s.add(model(**{k: _parse(cols[k], v) for k, v in row.items()}))
        counts[name] = len(tables[name])
    s.flush()
    return counts


def table_frames(s) -> dict[str, pd.DataFrame]:
    return {name: pd.DataFrame(rows) for name, rows in dump_all(s).items()}


def table_csv(s, name: str) -> str:
    return table_frames(s)[name].to_csv(index=False)


def to_excel(s) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        for name, df in table_frames(s).items():
            df.to_excel(xl, sheet_name=name[:31], index=False)
    return buf.getvalue()