import streamlit as st
from database.session import session_scope
from models.orm import User
from werkzeug.security import generate_password_hash

ready()

# Function to check if user is logged in
def is_user_logged_in():
    return "user" in st.session_state

# Function to login as test user
def login_as_test_user():
    with session_scope() as s:
        test_user = s.query(User).filter_by(email="test@example.com").first()
        if test_user:
            st.session_state["user"] = {
                "id": test_user.id,
                "name": test_user.name,
                "email": test_user.email,
            }
            st.experimental_rerun()

# Function to create a test user
def create_test_user():
    with session_scope() as s:
        # Check if the test user already exists
        existing_user = s.query(User).filter_by(email="test@example.com").first()
        if existing_user:
            print("Test user already exists.")
            return

        # Create and add the test user
        test_user = User(
            name="Test User",
            email="test@example.com",
            password=generate_password_hash("test123"),  # Hash the password
        )
        s.add(test_user)
        s.commit()
        print("Test user created successfully.")

# Sidebar
st.sidebar.title("Navigation")
if st.sidebar.button("Login as Test User"):
    login_as_test_user()

# Main app logic
if is_user_logged_in():
    # App content for logged-in users
    st.title(f"Welcome, {st.session_state['user']['name']}!")
    # ... rest of the app ...
else:
    # Onboarding or login screen
    # ... existing onboarding logic ...

if __name__ == "__main__":
    create_test_user()