from pathlib import Path
import pytest
from database.session import session_scope
from models.orm import RunningSession, Setting
from services import garmin_accounts, plan_service as ps
from tests.conftest import START


def test_disconnect_retain_imported_data(db, monkeypatch):
    monkeypatch.setattr(garmin_accounts, "_cipher", lambda: type("C", (), {
        "encrypt": lambda self, b: b"enc_" + b,
        "decrypt": lambda self, b: b.replace(b"enc_", b""),
    })())
    
    with session_scope() as s:
        user = ps.ensure_user(s, auth_uid="tp30-user-1")
        # Save credentials
        garmin_accounts.save_credentials(s, user, "user1@example.com", "pass1")
        
        # Add a linked run
        sess = RunningSession(
            user_id=user.id, date=START, planned_km=10.0, actual_km=10.5,
            garmin_id="g123", status="completed", source="garmin"
        )
        s.add(sess)
        s.flush()
        
        # Disconnect with retain_imported_data=True
        res = garmin_accounts.disconnect_account(s, user, retain_imported_data=True)
        assert res["credentials_removed"] is True
        assert res["imported_data_action"] == "retained"
        assert res["revocation_supported"] is False
        assert "To fully revoke Garmin's server-side access" in res["revocation_notice"]
        
        # Check credentials are deleted
        assert garmin_accounts.credentials(s, user) is None
        # Check session is still linked
        s.refresh(sess)
        assert sess.garmin_id == "g123"
        assert sess.actual_km == 10.5
        assert sess.status == "completed"


def test_disconnect_delete_imported_data(db, monkeypatch):
    monkeypatch.setattr(garmin_accounts, "_cipher", lambda: type("C", (), {
        "encrypt": lambda self, b: b"enc_" + b,
        "decrypt": lambda self, b: b.replace(b"enc_", b""),
    })())
    
    with session_scope() as s:
        user = ps.ensure_user(s, auth_uid="tp30-user-2")
        garmin_accounts.save_credentials(s, user, "user2@example.com", "pass2")
        
        sess = RunningSession(
            user_id=user.id, date=START, planned_km=10.0, actual_km=10.5,
            garmin_id="g456", status="completed", source="garmin"
        )
        s.add(sess)
        s.flush()
        
        # Disconnect with retain_imported_data=False
        res = garmin_accounts.disconnect_account(s, user, retain_imported_data=False)
        assert res["credentials_removed"] is True
        assert res["imported_data_action"] == "deleted_and_unlinked"
        
        # Check credentials deleted
        assert garmin_accounts.credentials(s, user) is None
        # Check session is unlinked
        s.refresh(sess)
        assert sess.garmin_id is None
        assert sess.actual_km is None
        assert sess.status == "planned"
        assert sess.source is None


def test_disconnect_account_isolation(db, monkeypatch):
    monkeypatch.setattr(garmin_accounts, "_cipher", lambda: type("C", (), {
        "encrypt": lambda self, b: b"enc_" + b,
        "decrypt": lambda self, b: b.replace(b"enc_", b""),
    })())
    
    with session_scope() as s:
        user1 = ps.ensure_user(s, auth_uid="tp30-iso-1")
        user2 = ps.ensure_user(s, auth_uid="tp30-iso-2")
        
        garmin_accounts.save_credentials(s, user1, "u1@test.com", "p1")
        garmin_accounts.save_credentials(s, user2, "u2@test.com", "p2")
        
        # Disconnect user1 only
        garmin_accounts.disconnect_account(s, user1, retain_imported_data=False)
        
        # User 1 credentials gone
        assert garmin_accounts.credentials(s, user1) is None
        # User 2 credentials intact
        creds2 = garmin_accounts.credentials(s, user2)
        assert creds2 is not None
        assert creds2[0] == "u2@test.com"
