import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models.orm import Base, User, PlanChangeHistory
from services import plan_service as ps
from datetime import datetime, timedelta

@pytest.fixture
def db_session():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    u1 = User(email="u1@example.com", name="User1")
    u2 = User(email="u2@example.com", name="User2")
    session.add_all([u1, u2])
    session.commit()
    session.u1 = u1
    session.u2 = u2
    yield session
    session.close()

def test_record_change(db_session):
    ps.record_plan_change(db_session, db_session.u1.id, db_session.u2.id, "test_field")
    history = ps.get_plan_change_history(db_session, db_session.u1.id, db_session.u2.id)
    assert len(history) == 1
    assert history[0].field_changed == "test_field"
    assert history[0].owner_id == db_session.u1.id
