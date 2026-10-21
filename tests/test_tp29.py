
import pytest
from database.session import session_scope
from models.orm import RunningSession
from services import garmin_link, plan_service as ps
from tests.conftest import START

def test_link_idempotency(db):
    with session_scope() as s:
        user = ps.ensure_user(s)
        # Create a planned session
        session = RunningSession(user_id=user.id, date=START, planned_km=10.0, status="planned")
        s.add(session)
        s.flush()
        
        run = {"id": "garmin_1", "distance_km": 10.0}
        
        # First link
        garmin_link.link(s, user, session.id, run)
        s.flush()
        assert session.garmin_id == "garmin_1"
        
        # Second link (idempotent)
        garmin_link.link(s, user, session.id, run)
        s.flush()
        assert session.garmin_id == "garmin_1"
        
        # Re-link to DIFFERENT run (should fail)
        run2 = {"id": "garmin_2", "distance_km": 10.0}
        with pytest.raises(ValueError, match="already linked"):
            garmin_link.link(s, user, session.id, run2)
