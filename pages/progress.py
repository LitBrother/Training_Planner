import streamlit as st
import pandas as pd

from database.session import session_scope
from services import plan_service as ps
from utils import charts
from utils.bootstrap import get_app_user, ready

ready()
st.title("📈 Progress")
with session_scope() as s:
    user = get_app_user(s)
    start = ps.get_settings(s, user).plan_start
    df = ps.weekly_dataframe(s, user)
    mdf = ps.monthly_dataframe(s, user)

df = df[pd.to_datetime(df["week_start"]) >= pd.Timestamp(start)]

st.plotly_chart(charts.weekly_volume_fig(df, st.session_state.get("garmin_runs")), use_container_width=True)
st.plotly_chart(charts.long_run_chart(df), use_container_width=True)
st.plotly_chart(charts.monthly_chart(mdf), use_container_width=True)
if df["logged"].any():
    st.plotly_chart(charts.consistency_chart(df), use_container_width=True)
else:
    st.info("Log some sessions in the Weekly Planner to see consistency.")
st.dataframe(df.drop(columns=["logged"]), use_container_width=True, hide_index=True)