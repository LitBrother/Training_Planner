import streamlit as st
from pydantic import ValidationError

from database.session import session_scope
from services.athlete_profile import get_profile, save_profile
from services.evidence import SOURCES
from utils.bootstrap import get_app_user, ready

ready()
st.title("Athlete profile")
st.caption("Use a self-reported profile to tailor training guidance. Age and body weight are optional.")
rerun = False

with session_scope() as s:
    user = get_app_user(s)
    profile = get_profile(s, user)
    with st.form("athlete_profile"):
        name = st.text_input("Name", user.name)
        focus = st.selectbox("Training focus", ["Running", "Strength", "Both"],
                             index=["Running", "Strength", "Both"].index(profile.training_focus))
        level = st.selectbox("Training experience", ["Beginner", "Intermediate", "Advanced"],
                             index=["Beginner", "Intermediate", "Advanced"].index(profile.experience_level))
        goal = st.text_input("Current goal", profile.goal, max_chars=300)
        share_age = st.checkbox("Save age in my profile", value=profile.age is not None)
        age = st.number_input("Age", min_value=18, max_value=100, value=profile.age or 30, disabled=not share_age)
        share_weight = st.checkbox("Save body weight in my profile",
                                   value=profile.body_weight_kg is not None)
        weight = st.number_input("Body weight (kg)", min_value=35.0, max_value=250.0,
                                 value=profile.body_weight_kg or 70.0, step=0.5, disabled=not share_weight)
        share_recovery = st.checkbox(
            "Remember my Garmin recovery-sharing preference (AI still asks for consent at each review)",
            value=profile.share_garmin_recovery_with_ai,
        )
        if st.form_submit_button("Save profile"):
            try:
                save_profile(s, user, {
                    "user_name": name,
                    "training_focus": focus,
                    "experience_level": level,
                    "age": int(age) if share_age else None,
                    "body_weight_kg": float(weight) if share_weight else None,
                    "goal": goal,
                    "share_garmin_recovery_with_ai": share_recovery,
                })
                st.success("Profile saved and your plan updated.")
                rerun = True
            except (ValidationError, ValueError) as exc:
                st.error(f"Profile not saved: {exc}")

st.caption("We do not estimate a biological/fitness age or infer potential from age or body weight. "
           "Age and body weight are saved only in your profile and are not sent to Gemini. Strength progression "
           "uses your logged sets, reps and weights; body weight is not used to prescribe loads. Garmin recovery "
           "metrics are omitted unless you opt in separately on each AI review.")
with st.expander("Training evidence used for consistent guidance"):
    st.write("The coach uses this fixed set of published sources and cites source IDs in its advice.")
    for source in SOURCES:
        st.markdown(f"**{source['id']}** — {source['citation']}")
        st.write(source["principle"])
        st.markdown(f"[View publication]({source['url']})")

if rerun:
    st.rerun()
