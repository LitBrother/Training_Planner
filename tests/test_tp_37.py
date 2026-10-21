import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models.orm import Base, User, Friendship
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

def test_friend_request_expiry(db_session):
    request = Friendship(user_id=db_session.u1.id, friend_id=db_session.u2.id, requested_by=db_session.u1.id, status="pending", expires_at=datetime.now() - timedelta(days=1))
    db_session.add(request)
    db_session.commit()
    
    with pytest.raises(ValueError, match="Incoming friend request not found or expired."):
        ps.respond_friend_request(db_session, db_session.u2, request.id, True)
