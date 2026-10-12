from datetime import date

import plotly.express as px
import streamlit as st
from sqlalchemy import select

from database.session import session_scope
from models.orm import StrengthSession
from services import coaching
from services import plan_service as ps
from services import strength_progress as sp
from utils.bootstrap import ready

ready()
st.title("🏋️ Strength")

st.subheader("Lift progression")
with session_scope() as s:
    user = ps.ensure_user(s)
    with st.form("lift"):
        c = st.columns([3, 2, 1, 1, 2])
        ex = c[0].text_input("Exercise", placeholder="Bench Press")
        wt = c[1].number_input("Weight (kg)", 0.0, 500.0, 20.0, 2.5)
        sets = c[2].number_input("Sets", 1, 10, 3)
        reps = c[3].number_input("Reps", 1, 50, 8)
        when = c[4].date_input("Date", date.today())
        if st.form_submit_button("Log lift") and ex.strip():
            sp.add_log(s, user, when, ex, wt, int(sets), int(reps))
            st.rerun()
    df = sp.log_frame(s, user)

if df.empty:
    st.info("Log a lift to see progression.")
else:
    pick = st.selectbox("Exercise", sorted(df["exercise"].unique()))
    if pick:
        runs = st.session_state.get("garmin_runs") or []
        acwr = coaching.analyze(runs, date.today())["acwr"] if runs else None
        hold = bool(acwr and acwr > 1.3)
        st.success(sp.suggest_next(df, str(pick), hold))
        d = df[df["exercise"] == pick]
        fig = px.line(d, x="date", y="est_1rm", markers=True, template="plotly_dark",
                      title=f"{pick}: estimated 1RM (kg)", color_discrete_sequence=["#00D4AA"])
        st.plotly_chart(fig, use_container_width=True)
        best = df.groupby("exercise")["est_1rm"].max().reset_index()
        best["Best est. 1RM"] = best["est_1rm"]
        st.dataframe(best[["exercise", "Best est. 1RM"]], hide_index=True)