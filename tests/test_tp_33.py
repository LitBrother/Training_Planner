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
    # Create users
    u1 = User(email="u1@example.com", name="User1")
    u2 = User(email="u2@example.com", name="User2")
    session.add_all([u1, u2])
    session.commit()
    session.u1 = u1
    session.u2 = u2
    yield session
    session.close()

def test_default_no_sharing(db_session):
    # Create friendship
    friendship = Friendship(user_id=db_session.u1.id, friend_id=db_session.u2.id, requested_by=db_session.u1.id, status="accepted")
    db_session.add(friendship)
    db_session.commit()
    
    # Verify no automatic sharing
    assert not friendship.viewer
    assert not friendship.editor

def test_grant_viewer_role(db_session):
    friendship = Friendship(user_id=db_session.u1.id, friend_id=db_session.u2.id, requested_by=db_session.u1.id, status="accepted")
    db_session.add(friendship)
    db_session.commit()
    
    ps.update_permissions(db_session, db_session.u1.id, db_session.u2.id, viewer=True, editor=False)
    db_session.refresh(friendship)
    
    assert friendship.viewer
    assert not friendship.editor
    
def test_grant_editor_role(db_session):
    friendship = Friendship(user_id=db_session.u1.id, friend_id=db_session.u2.id, requested_by=db_session.u1.id, status="accepted")
    db_session.add(friendship)
    db_session.commit()
    
    ps.update_permissions(db_session, db_session.u1.id, db_session.u2.id, viewer=True, editor=True)
    db_session.refresh(friendship)
    
    assert friendship.viewer
    assert friendship.editor
