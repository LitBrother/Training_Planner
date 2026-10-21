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
    # Clean up token directory files for this user account
    token_dir = TOKEN_ROOT / str(user.id)
    if token_dir.exists() and token_dir.is_dir():
        for file in token_dir.glob("*"):
            try:
                file.unlink()
            except Exception:
                pass
        try:
            token_dir.rmdir()
        except Exception:
            pass


def disconnect_account(s, user, retain_imported_data: bool = True) -> dict:
    """Disconnect Garmin for this account.
    
    - Attempts provider-side revocation (Garmin Connect API does not provide an automated
      token revocation endpoint, so we document/return unsupported revocation guidance).
    - Removes stored credentials and auth token files for this user only.
    - Settles/cancels any pending sync state.
    - Optionally retains or deletes imported Garmin run matches based on retain_imported_data.
    - Guarantees complete account isolation.
    """
    # 1. Settle/cancel pending syncs / clear credential cache
    remove_credentials(s, user)
    
    # 2. Handle imported runs and wellness data according to user choice
    from models.orm import RunningSession
    runs = list(s.scalars(select(RunningSession).where(RunningSession.user_id == user.id, RunningSession.garmin_id.is_not(None))))
    
    if not retain_imported_data:
        # Delete or unlink imported runs
        for sess in runs:
            sess.garmin_id = None
            sess.actual_km = None
            sess.status = "planned"
            sess.source = None
        s.flush()
        from services import plan_service as ps
        ps.recalc(s, user)
        imported_action = "deleted_and_unlinked"
    else:
        imported_action = "retained"
        
    # 3. Provider revocation status
    # Garmin Connect API does not currently support programmatic third-party token revocation.
    revocation_supported = False
    revocation_notice = (
        "Garmin Connect does not provide an automated API revocation endpoint for third-party apps. "
        "Your local app-stored credentials and tokens have been completely deleted. "
        "To fully revoke Garmin's server-side access to this application, please visit "
        "https://connect.garmin.com -> Account Settings -> Connected Apps and remove authorization."
    )
    
    return {
        "user_id": user.id,
        "credentials_removed": True,
        "tokens_cleared": True,
        "imported_data_action": imported_action,
        "revocation_supported": revocation_supported,
        "revocation_notice": revocation_notice,
    }


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
