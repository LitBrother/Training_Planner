from typing import Optional

from sqlalchemy import select

from database.session import session_scope
from models.orm import User, TrainingWeek, RunningSession, StrengthSession, StrengthLog, Note, AIAdjustment, AthleteProfile, Setting, Event, RunCorrectionHistory, StrengthSetDetail
from utils.bootstrap import get_app_user


class RestorePreviewError(RuntimeError):
    pass


def preview_restore(s, user: User, json_text: str) -> dict:
    """Parse and validate a backup file without modifying data.

    Returns a preview of what would be restored for this account.
    """    try:
        import json
        data = json.loads(json_text)
    except Exception as exc:
        raise RestorePreviewError(f"Invalid JSON: {exc}") from exc

    tables = data.get("tables", {})
    if not isinstance(tables, dict):
        raise RestorePreviewError("Backup file is malformed.")

    # Verify schema version
    if data.get("schema_version") != 1:
        raise RestorePreviewError("Unsupported backup format version.")

    # Count what would be restored
    preview = {
        "users": 0,
        "athlete_profile": 0,
        "training_weeks": 0,
        "running_sessions": 0,
        "strength_sessions": 0,
        "strength_logs": 0,
        "strength_set_details": 0,
        "notes": 0,
        "events": 0,
        "ai_adjustments": 0,
        "settings": 0,
        "run_correction_history": 0,
    }

    # Validate user matches
    user_rows = tables.get("users", [])
    if not user_rows or not isinstance(user_rows, list) or len(user_rows) != 1:
        raise RestorePreviewError("Backup must contain exactly one user record.")
    user_row = user_rows[0]
    if not isinstance(user_row, dict):
        raise RestorePreviewError("User record is malformed.")
    if user_row.get("id") != user.id:
        raise RestorePreviewError("Backup user ID does not match current account.")

    # Validate tables exist and are lists
    required_tables = {
        "training_weeks", "running_sessions", "strength_sessions",
        "strength_logs", "notes", "events", "ai_adjustments", "settings"
    }
    for table_name in required_tables:
        if table_name not in tables or not isinstance(tables[table_name], list):
            raise RestorePreviewError(f"Missing or invalid table: {table_name}")

    # Count items
    preview["users"] = 1
    preview["athlete_profile"] = len(tables.get("athlete_profiles", []))
    preview["training_weeks"] = len(tables.get("training_weeks", []))
    preview["running_sessions"] = len(tables.get("running_sessions", []))
    preview["strength_sessions"] = len(tables.get("strength_sessions", []))
    preview["strength_logs"] = len(tables.get("strength_logs", []))
    preview["strength_set_details"] = len(tables.get("strength_set_details", []))
    preview["notes"] = len(tables.get("notes", []))
    preview["events"] = len(tables.get("events", []))
    preview["ai_adjustments"] = len(tables.get("ai_adjustments", []))
    preview["settings"] = len(tables.get("settings", []))
    preview["run_correction_history"] = len(tables.get("run_correction_history", []))

    # Verify data bounds
    for run in tables.get("running_sessions", []):
        if not isinstance(run, dict):
            raise RestorePreviewError("Invalid running session data.")
        if not (0 <= run.get("planned_km", 0) <= 200):
            raise RestorePreviewError("Planned distance out of bounds.")
    for log in tables.get("strength_logs", []):
        if not (0 <= log.get("weight_kg", 0) <= 500):
            raise RestorePreviewError("Strength weight out of bounds.")

    # Check for sensitive data
    for table_name, rows in tables.items():
        for row in rows:
            if isinstance(row, dict):
                if "auth_uid" in row or "email" in row:
                    raise RestorePreviewError("Backup contains sensitive account data. Use a newer export.")

    return preview


def restore_with_preview(s, user: User, json_text: str) -> dict:
    """Restore after showing preview and confirmation.

    Returns counts of restored items.
    """
    preview = preview_restore(s, user, json_text)
    # Additional confirmation check would go here
    # Perform actual restore (reuse existing export_service.restore_json logic)
    from services.export_service import restore_json
    return restore_json(s, json_text, user)
