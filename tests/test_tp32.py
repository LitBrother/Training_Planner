import pytest
from database.session import session_scope
from services import ai_service as ai, athlete_profile as ap, plan_service as ps

def test_wellness_revocation_blocks_transmission(db):
    with session_scope() as s:
        user = ps.ensure_user(s)
        profile = ap.get_profile(s, user)
        profile.share_garmin_recovery_with_ai = False
        s.flush()
        
        # Should be blocked
        ok, msg = ai.verify_ai_wellness_consent(s, user, include_garmin_recovery=True)
        assert ok is False
        assert "blocked" in msg
        
        # Should be allowed if False
        ok, msg = ai.verify_ai_wellness_consent(s, user, include_garmin_recovery=False)
        assert ok is True

def test_wellness_revocation_enables_transmission(db):
    with session_scope() as s:
        user = ps.ensure_user(s)
        profile = ap.get_profile(s, user)
        profile.share_garmin_recovery_with_ai = True
        s.flush()
        
        # Should be allowed
        ok, msg = ai.verify_ai_wellness_consent(s, user, include_garmin_recovery=True)
        assert ok is True
