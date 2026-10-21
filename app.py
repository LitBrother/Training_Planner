import streamlit as st
from pydantic import ValidationError

st.set_page_config(page_title="Training Planner", page_icon="🏃", layout="wide")

from utils.bootstrap import ready  # noqa: E402
from database.session import session_scope  # noqa: E402
from services.athlete_profile import get_profile, save_profile  # noqa: E402
from utils.bootstrap import get_app_user  # noqa: E402

ready()

with session_scope() as s:
    athlete = get_app_user(s)
    profile = get_profile(s, athlete)
    needs_onboarding = True  # Temporarily force onboarding for all users
    profile_values = {
        "user_name": athlete.name,
        "training_focus": profile.training_focus,
        "experience_level": profile.experience_level,
        "age": profile.age,
        "body_weight_kg": profile.body_weight_kg,
        "goal": profile.goal,
    }
if needs_onboarding:
    st.title("Welcome to your Training Planner")
    st.write("Set up a starting profile. You can change it later; the race date is optional.")
    with st.form("athlete_onboarding"):
        name = st.text_input("What should we call you?", profile_values["user_name"])
        focus = st.radio(
            "What do you want to train?",
            ["Running", "Strength", "Both"],
            format_func=lambda value: {
                "Running": "Running",
                "Strength": "Strength training",
                "Both": "Running and strength",
            }[value],
            horizontal=True,
        )
        level = st.selectbox("How would you describe your current training experience?",
                             ["Beginner", "Intermediate", "Advanced"])
        goal = st.text_input("What is your goal?", profile_values["goal"],
                             placeholder="e.g. build a consistent routine or prepare for a 10K")
        st.caption("Age and body weight are optional. They are not used to calculate a fitness age or prescribe "
                   "lifting weights; recommendations use your recorded training and your own goals.")
        include_age = st.checkbox("Share my age (optional)", value=profile_values["age"] is not None)
        age = st.number_input("Age", min_value=18, max_value=100,
                              value=int(profile_values["age"] or 30), disabled=not include_age)
        include_weight = st.checkbox("Share my body weight (optional)",
                                     value=profile_values["body_weight_kg"] is not None)
        weight = st.number_input("Body weight (kg)", min_value=35.0, max_value=250.0,
                                 value=float(profile_values["body_weight_kg"] or 70.0),
                                 step=0.5, disabled=not include_weight)
        share_recovery = st.checkbox(
            "Allow synced Garmin sleep and recovery metrics to be sent to Gemini when I request coaching",
            value=False,
        )
        submitted = st.form_submit_button("Create my profile", type="primary")
    if submitted:
        try:
            with session_scope() as s:
                save_profile(s, get_app_user(s), {
                    "user_name": name, "training_focus": focus, "experience_level": level,
                    "age": int(age) if include_age else None,
                    "body_weight_kg": float(weight) if include_weight else None,
                    "goal": goal,
                    "share_garmin_recovery_with_ai": share_recovery,
                })
            st.rerun()
        except (ValidationError, ValueError) as exc:
            st.error(f"Could not save your profile: {exc}")
    st.stop()

pages = [
    st.Page("pages/dashboard.py", title="Dashboard", icon="📊", default=True),
    st.Page("pages/training_plan.py", title="Weekly Planner", icon="🗓️"),
    st.Page("pages/notes.py", title="Notes", icon="📝"),
    st.Page("pages/strength.py", title="Strength", icon="🏋️"),
    st.Page("pages/progress.py", title="Progress", icon="📈"),
    st.Page("pages/events.py", title="Events", icon="🏁"),
    st.Page("pages/friends.py", title="Friends", icon="👥"),
    st.Page("pages/ai_coach.py", title="AI Coach", icon="🤖"),
    st.Page("pages/settings.py", title="Settings", icon="⚙️"),
    st.Page("pages/profile.py", title="Athlete Profile", icon="🧑‍💻"),
    st.Page("pages/events.py", title="Events", icon="🏁"),
    st.Page("pages/ai_coach.py", title="AI Coach", icon="🤖"),
    st.Page("pages/progress.py", title="Progress", icon="📈"),
    st.Page("pages/notes.py", title="Notes", icon="📝"),
    st.Page("pages/strength.py", title="Strength", icon="🏋️"),
    st.Page("pages/friends.py", title="Friends", icon="👥"),
    st.Page("pages/profile.py", title="Athlete Profile", icon="🧑‍💻"),
    st.Page("pages/settings.py", title="Settings", icon="⚙️"),
]
st.navigation(pages).run()
st.sidebar.divider()
if st.sidebar.button("Sign out"):
    st.session_state.clear()
    st.rerun()
st.sidebar.caption("Not medical advice. Consult a qualified professional about injuries or health concerns.")