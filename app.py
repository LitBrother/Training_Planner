import streamlit as st

st.set_page_config(page_title="Adam's Marathon Planner", page_icon="🏃", layout="wide")

from utils.bootstrap import ready  # noqa: E402

ready()

pages = [
    st.Page("pages/dashboard.py", title="Dashboard", icon="📊", default=True),
    st.Page("pages/training_plan.py", title="Weekly Planner", icon="🗓️"),
    st.Page("pages/events.py", title="Events", icon="🏁"),
    st.Page("pages/ai_coach.py", title="AI Coach", icon="🤖"),
    st.Page("pages/progress.py", title="Progress", icon="📈"),
    st.Page("pages/notes.py", title="Notes", icon="📝"),
    st.Page("pages/strength.py", title="Strength", icon="🏋️"),
    st.Page("pages/friends.py", title="Friends", icon="👥"),
    st.Page("pages/settings.py", title="Settings", icon="⚙️"),
]
st.navigation(pages).run()
st.sidebar.caption("Not medical advice. Consult a qualified professional about injuries or health concerns.")