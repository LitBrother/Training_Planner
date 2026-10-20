import io
import json
import math
import re
from datetime import date, datetime, timezone
from numbers import Real

import pandas as pd
from sqlalchemy import Date, DateTime, inspect, select

from models.orm import (
    AIAdjustment, AthleteProfile, Event, Note, RunCorrectionHistory, RunningSession,
    Setting, StrengthLog, StrengthSession, StrengthSetDetail, TrainingWeek, User,
)
from models.schemas import PlanSettings

TABLES = {
    "users": User,
    "settings": Setting,
    "training_weeks": TrainingWeek,
    "running_sessions": RunningSession,
    "run_correction_history": RunCorrectionHistory,
    "strength_sessions": StrengthSession,
    "strength_logs": StrengthLog,
    "strength_set_details": StrengthSetDetail,
    "events": Event,
    "notes": Note,
    "ai_adjustments": AIAdjustment,
    "athlete_profiles": AthleteProfile,
}

OPTIONAL_BACKUP_TABLES = {
    "strength_logs", "athlete_profiles", "strength_set_details", "run_correction_history",
}
SAFE_SETTING_KEYS = frozenset(PlanSettings.model_fields)
SENSITIVE_SETTING_VALUE = re.compile(
    r"(?:credential|token|secret|password|passwd|api[_ -]?key|authorization|bearer|"
    r"session|cookie|private[_ -]?key|encryption[_ -]?key|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----)",
    re.IGNORECASE,
)
EXCLUDED_FIELDS = {
    "users": {"auth_uid", "email"},
    "ai_adjustments": {"created_by_user_id"},
}
NON_JSON_EXCLUDED_FIELDS = {"source", "garmin_id"}

TABLE_CATEGORIES = {
    "users": "profile name",
    "settings": "approved plan preferences",
    "athlete_profiles": "athlete profile",
    "training_weeks": "training plans and week summaries",
    "running_sessions": "run records, including imported Garmin runs",
    "run_correction_history": "actual running-distance correction history",
    "strength_sessions": "planned strength sessions",
    "strength_logs": "logged strength aggregate and summary fields",
    "strength_set_details": "logged strength per-set detail",
    "events": "events",
    "notes": "notes",
    "ai_adjustments": "AIAdjustment records",
}

BASE_EXCLUDED_CATEGORIES = [
    "AI conversation/prompt history",
    "raw Garmin caches and wellness/recovery data",
    "friendships and other accounts' records",
    "AIAdjustment creator IDs",
    "auth_uid, email, credentials, tokens, secrets, encryption keys, and session data",
]


def _ser(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def export_table_names(export_format: str, table_name: str | None = None) -> list[str]:
    normalized = export_format.lower()
    if normalized == "csv":
        if table_name not in TABLES:
            raise ValueError("Choose a supported table for CSV export.")
        return [table_name]
    if normalized in {"excel", "json"}:
        return list(TABLES)
    raise ValueError("Export format must be CSV, Excel, or JSON.")


def _columns(model, table_name: str, export_format: str) -> list[str]:
    excluded = set(EXCLUDED_FIELDS.get(table_name, set()))
    if export_format.lower() != "json":
        excluded.update(NON_JSON_EXCLUDED_FIELDS)
    return [
        column.key for column in inspect(model).column_attrs
        if column.key not in excluded
    ]


def _rows_for_table(s, table_name: str, model, user, export_format: str) -> list[dict]:
    query = select(model)
    if user is not None:
        query = query.where(
            model.id == user.id if model is User else model.user_id == user.id
        )
    columns = _columns(model, table_name, export_format)
    rows = []
    for obj in s.scalars(query):
        if model is Setting and not _exportable_setting(obj.key, obj.value):
            continue
        row = {}
        for key in columns:
            value = getattr(obj, key)
            if model is RunCorrectionHistory and key == "corrected_at" and value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            row[key] = _ser(value)
        rows.append(row)
    return rows


def _exportable_setting(key: object, value: object) -> bool:
    """Only export known plan preferences, and never values that look like secrets."""
    return (
        isinstance(key, str)
        and key in SAFE_SETTING_KEYS
        and isinstance(value, str)
        and not SENSITIVE_SETTING_VALUE.search(value)
    )


def _require_owner(user) -> None:
    if user is None:
        raise ValueError("An authenticated account is required for export or restore.")


def dump_export(s, export_format: str, user=None, table_name: str | None = None) -> dict:
    _require_owner(user)
    names = export_table_names(export_format, table_name)
    return {
        name: _rows_for_table(s, name, TABLES[name], user, export_format)
        for name in names
    }


def dump_all(s, user=None) -> dict:
    """Backward-compatible JSON-shaped, account-scoped table serializer."""
    return dump_export(s, "json", user)


def to_json(s, user=None) -> str:
    return json.dumps({
        "app": "Training Planner",
        "version": 1,
        "exported_at": datetime.now().isoformat(),
        "tables": dump_export(s, "json", user),
    }, indent=2)


def _parse(column, value):
    if value is None:
        return None
    if isinstance(column.type, DateTime):
        parsed = datetime.fromisoformat(value)
        if column.type.timezone and parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    if isinstance(column.type, Date):
        return date.fromisoformat(value)
    return value


def _accepted_backup_tables() -> set[frozenset[str]]:
    required = set(TABLES) - OPTIONAL_BACKUP_TABLES
    optional = OPTIONAL_BACKUP_TABLES
    return {
        frozenset(required | included)
        for mask in range(1 << len(optional))
        for included in (
            {name for bit, name in enumerate(sorted(optional)) if mask & (1 << bit)},
        )
    }


def _validate_detail_parent_ids(tables: dict, user) -> None:
    parent_rows = {row.get("id"): row for row in tables["strength_logs"]}
    details_by_parent: dict[object, list[dict]] = {}
    for detail in tables["strength_set_details"]:
        parent = parent_rows.get(detail.get("strength_log_id"))
        if parent is None or (
            user is None and parent.get("user_id") != detail.get("user_id")
        ):
            raise ValueError(
                "Invalid backup file: per-set detail has no matching strength log."
            )
        details_by_parent.setdefault(detail.get("strength_log_id"), []).append(detail)
        weight = detail.get("weight_kg")
        reps = detail.get("reps")
        set_number = detail.get("set_number")
        if (
            not isinstance(weight, Real) or isinstance(weight, bool)
            or not math.isfinite(weight) or not 0 <= weight <= 500
            or not math.isclose(
                weight * 10, round(weight * 10), rel_tol=0, abs_tol=1e-7
            )
            or not isinstance(reps, int) or isinstance(reps, bool) or not 1 <= reps <= 50
            or not isinstance(set_number, int) or isinstance(set_number, bool)
            or not 1 <= set_number <= 10
        ):
            raise ValueError("Invalid backup file: per-set detail is outside supported bounds.")
    for details in details_by_parent.values():
        set_numbers = sorted(row["set_number"] for row in details)
        if len(details) > 10 or set_numbers != list(range(1, len(details) + 1)):
            raise ValueError("Invalid backup file: per-set detail numbering is invalid.")


def _validate_run_correction_parent_ids(tables: dict) -> None:
    if (
        not isinstance(tables.get("running_sessions"), list)
        or not isinstance(tables.get("run_correction_history"), list)
        or any(not isinstance(row, dict) for row in tables["running_sessions"])
        or any(not isinstance(row, dict) for row in tables["run_correction_history"])
    ):
        raise ValueError("Invalid backup file: run correction history is invalid.")
    runs = {row.get("id"): row for row in tables["running_sessions"]}
    for correction in tables["run_correction_history"]:
        run = runs.get(correction.get("running_session_id"))
        old_value = correction.get("old_value")
        new_value = correction.get("new_value")
        corrected_at = correction.get("corrected_at")
        if (
            run is None
            or run.get("user_id") != correction.get("user_id")
            or correction.get("field_name") != "actual_km"
            or not isinstance(old_value, Real) or isinstance(old_value, bool)
            or not math.isfinite(old_value) or not 0 < old_value <= 100
            or not isinstance(new_value, Real) or isinstance(new_value, bool)
            or not math.isfinite(new_value) or not 0 < new_value <= 100
            or not isinstance(corrected_at, str)
        ):
            raise ValueError("Invalid backup file: run correction history is invalid.")
        try:
            datetime.fromisoformat(corrected_at)
        except ValueError:
            raise ValueError("Invalid backup file: run correction timestamp is invalid.") from None


def restore_json(s, text: str, user=None) -> dict[str, int]:
    _require_owner(user)
    data = json.loads(text)
    tables = data.get("tables") if isinstance(data, dict) else None
    if not isinstance(tables, dict) or frozenset(tables) not in _accepted_backup_tables():
        raise ValueError("Invalid backup file: tables do not match this application.")
    has_correction_history = "run_correction_history" in tables
    for optional_table in OPTIONAL_BACKUP_TABLES:
        tables.setdefault(optional_table, [])
    _validate_detail_parent_ids(tables, user)
    _validate_run_correction_parent_ids(tables)

    if user is not None:
        if not has_correction_history and s.scalar(select(RunCorrectionHistory.id).where(
            RunCorrectionHistory.user_id == user.id
        ).limit(1)) is not None:
            raise ValueError(
                "This account has run correction history; restore a backup that includes it."
            )
        counts = {"users": 1}
        # Explicitly remove children before parents; SQLite foreign-key enforcement can be off.
        own_details = s.scalars(select(StrengthSetDetail).where(
            StrengthSetDetail.user_id == user.id
        )).all()
        for detail in own_details:
            s.delete(detail)
        models = [TABLES[name] for name in TABLES if name != "users"]
        for model in reversed(models):
            query = select(model).where(model.user_id == user.id)
            objects = s.scalars(query).all()
            if model is Setting:
                objects = [
                    obj for obj in objects
                    if _exportable_setting(obj.key, obj.value)
                ]
            for obj in objects:
                s.delete(obj)
        s.flush()

        week_ids = {}
        run_ids = {}
        strength_log_ids = {}
        for name, model in TABLES.items():
            if model is User:
                continue
            restored_count = 0
            for row in tables[name]:
                if model is Setting and not _exportable_setting(
                    row.get("key"), row.get("value")
                ):
                    continue
                values = {
                    key: _parse(model.__table__.columns[key], value)
                    for key, value in row.items()
                    if key in model.__table__.columns
                    and key not in {"id", "user_id"}
                    and not (
                        model is AIAdjustment and key == "created_by_user_id"
                    )
                    and not (
                        model is StrengthSetDetail and key == "strength_log_id"
                    )
                }
                values["user_id"] = user.id
                if model in {RunningSession, StrengthSession}:
                    old_week_id = row.get("week_id")
                    values["week_id"] = (
                        week_ids.get(old_week_id) if old_week_id is not None else None
                    )
                if model is RunCorrectionHistory:
                    old_run_id = row.get("running_session_id")
                    new_run_id = run_ids.get(old_run_id)
                    if new_run_id is None:
                        raise ValueError(
                            "Invalid backup file: run correction has no matching run."
                        )
                    values["running_session_id"] = new_run_id
                if model is StrengthSetDetail:
                    old_parent_id = row.get("strength_log_id")
                    new_parent_id = strength_log_ids.get(old_parent_id)
                    if new_parent_id is None:
                        raise ValueError(
                            "Invalid backup file: per-set detail has no matching strength log."
                        )
                    values["strength_log_id"] = new_parent_id
                obj = model(**values)
                s.add(obj)
                s.flush()
                if model is TrainingWeek:
                    week_ids[row.get("id")] = obj.id
                elif model is RunningSession:
                    run_ids[row.get("id")] = obj.id
                elif model is StrengthLog:
                    strength_log_ids[row.get("id")] = obj.id
                restored_count += 1
            counts[name] = restored_count
        # The archive contains only a profile name from User; keep all identity
        # attributes bound to the already-authenticated account.
        if tables["users"] and isinstance(tables["users"][0], dict):
            restored_name = tables["users"][0].get("name")
            if isinstance(restored_name, str) and 0 < len(restored_name) <= 100:
                user.name = restored_name
        return counts

    raise ValueError("An authenticated account is required for export or restore.")


def table_frames(s, user=None, export_format: str = "json") -> dict[str, pd.DataFrame]:
    return {
        name: pd.DataFrame(rows)
        for name, rows in dump_export(s, export_format, user).items()
    }


def table_csv(s, name: str, user=None) -> str:
    data = dump_export(s, "csv", user, name)[name]
    return pd.DataFrame(data).to_csv(index=False)


def to_excel(s, user=None) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as workbook:
        for name, frame in table_frames(s, user, "excel").items():
            frame.to_excel(workbook, sheet_name=name[:31], index=False)
    return buf.getvalue()


def export_manifest(export_format: str, user=None, table_name: str | None = None) -> dict:
    _require_owner(user)
    names = export_table_names(export_format, table_name)
    included = sorted({TABLE_CATEGORIES[name] for name in names})
    excluded_tables = sorted(set(TABLES) - set(names))
    excluded_categories = sorted({TABLE_CATEGORIES[name] for name in excluded_tables})
    if export_format.lower() == "csv":
        excluded_categories.append("Garmin source and activity identifiers")
    elif export_format.lower() == "excel":
        excluded_categories.append("Garmin source and activity identifiers")
    excluded_categories.extend(BASE_EXCLUDED_CATEGORIES)
    excluded_categories.append(
        "unknown and sensitive settings (only approved plan preferences are included)"
    )
    if export_format.lower() == "json":
        garmin_id_status = "Included with run records (source and activity ID) for idempotent restore."
    elif export_format.lower() == "excel":
        garmin_id_status = "Garmin source and activity IDs are excluded."
    else:
        garmin_id_status = "Garmin source and activity IDs are excluded."
    return {
        "format": export_format.upper(),
        "schema_version": 1,
        "scope": "Current signed-in account only; no other accounts.",
        "tables": names,
        "included": included,
        "excluded": list(dict.fromkeys(excluded_categories)),
        "garmin_identifiers": garmin_id_status,
        "restore": (
            "JSON restore replaces only the current account's exported data. "
            "Saved Garmin credential settings are excluded and remain preserved by restore. "
            "Unknown/non-allowlisted setting entries are also excluded and preserved."
        ),
    }


def manifest_text(export_format: str, user=None, table_name: str | None = None) -> str:
    manifest = export_manifest(export_format, user, table_name)
    included = ", ".join(manifest["included"]) or "none"
    tables = ", ".join(manifest["tables"])
    excluded = ", ".join(manifest["excluded"])
    table_suffix = (
        f" (selected table: {table_name})" if manifest["format"] == "CSV" else ""
    )
    return (
        f"**{manifest['format']} schema v{manifest['schema_version']}{table_suffix}**  \n"
        f"**Scope:** {manifest['scope']}  \n"
        f"**Included tables:** {tables}.  \n"
        f"**Included categories:** {included}. {manifest['garmin_identifiers']}  \n"
        f"**Excluded:** {excluded}.  \n"
        f"{manifest['restore']}"
    )
