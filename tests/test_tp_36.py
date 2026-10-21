import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models.orm import Base, User, Friendship
from services import plan_service as ps

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

def test_remove_friend_blocks_access(db_session):
    friendship = Friendship(user_id=db_session.u1.id, friend_id=db_session.u2.id, requested_by=db_session.u1.id, status="accepted")
    db_session.add(friendship)
    db_session.commit()
    
    ps.remove_friend(db_session, db_session.u1, db_session.u2.id)
    
    with pytest.raises(ValueError, match="Only an accepted friend can view or edit this plan."):
        ps.assert_friend_access(db_session, db_session.u2, db_session.u1.id)
