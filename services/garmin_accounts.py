from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from models.orm import Setting
from services.garmin_sync import fetch_runs, fetch_wellness

TOKEN_ROOT = Path(__file__).resolve().parent.parent / ".garmin_tokens"


def _cipher() -> Fernet:
    try:
        key = str(st.secrets.get("GARMIN_ENCRYPTION_KEY", "")).strip()
    except StreamlitSecretNotFoundError:
        key = ""
    if not key:
        raise RuntimeError(
            "Per-account Garmin linking needs GARMIN_ENCRYPTION_KEY in Streamlit Secrets. "
            "Generate a Fernet key and add it before saving credentials."
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise RuntimeError("GARMIN_ENCRYPTION_KEY is invalid; it must be a Fernet key.") from exc


def _rows(s, user) -> dict[str, Setting]:
    return {
        row.key: row for row in s.scalars(select(Setting).where(
            Setting.user_id == user.id,
            Setting.key.in_(("garmin_email_enc", "garmin_password_enc")),
        ))
    }


def credentials(s, user) -> tuple[str, str] | None:
    rows = _rows(s, user)
    email_row, password_row = rows.get("garmin_email_enc"), rows.get("garmin_password_enc")
    if not email_row or not password_row:
        return None
    try:
        cipher = _cipher()
        return (
            cipher.decrypt(email_row.value.encode()).decode(),
            cipher.decrypt(password_row.value.encode()).decode(),
        )
    except (InvalidToken, UnicodeDecodeError) as exc:
        raise RuntimeError("Saved Garmin credentials could not be decrypted. Check the encryption key.") from exc


def save_credentials(s, user, email: str, password: str) -> None:
    if not email.strip() or not password:
        raise ValueError("Enter both your Garmin email and password.")
    cipher = _cipher()
    rows = _rows(s, user)
    values = {
        "garmin_email_enc": cipher.encrypt(email.strip().encode()).decode(),
        "garmin_password_enc": cipher.encrypt(password.encode()).decode(),
    }
    for key, value in values.items():
        if key in rows:
            rows[key].value = value
        else:
            s.add(Setting(user_id=user.id, key=key, value=value))
    s.flush()


def remove_credentials(s, user) -> None:
    for row in _rows(s, user).values():
        s.delete(row)
    s.flush()


def fetch_account_runs(s, user, days: int = 35) -> list[dict]:
    details = credentials(s, user)
    if not details:
        raise RuntimeError("Save your Garmin connection in Settings first.")
    email, password = details
    return fetch_runs(email, password, days, TOKEN_ROOT / str(user.id))


def fetch_account_wellness(s, user, days: int = 7) -> dict:
    details = credentials(s, user)
    if not details:
        raise RuntimeError("Save your Garmin connection in Settings first.")
    email, password = details
    return fetch_wellness(email, password, days, TOKEN_ROOT / str(user.id))
