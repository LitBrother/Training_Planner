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

st.caption("Weekly running totals are in km. Completion (%) = Actual ÷ Adjusted "
           "(capped at 100%; 0% when Adjusted is 0).")
fig = charts.weekly_volume_fig(df, st.session_state.get("garmin_runs"))
st.plotly_chart(fig, use_container_width=True)
# TP-39: Add text summary for screen readers and narrow viewport
st.markdown("### Weekly Volume Summary")
st.markdown(f"""Total planned: {df['planned'].sum():.1f} km
Total adjusted: {df['adjusted'].sum():.1f} km
Total actual: {df['actual'].sum():.1f} km""")
# TP-40: Accessibility table
st.markdown("### Weekly Volume Table")
st.markdown("<small><table><thead><tr><th>Week</th><th>Planned</th><th>Adjusted</th><th>Actual</th><th>Completion</th></tr></thead><tbody>" + "".join(f"<tr><td>{row['week_start']:%Y-%m-%d}</td><td>{row['planned']:.1f}</td><td>{row['adjusted']:.1f}</td><td>{row['actual']:.1f}</td><td>{row['completion']:.0f}%</td></tr>" for _, row in df.iterrows()) + "</tbody></table></small>")""")
st.caption("In the weekly volume chart, Actual may fall back to Garmin weekly totals when recorded running Actual "
           "is zero or missing. The table and Completion use recorded plan actuals; unrecorded or missed runs "
           "contribute zero.")
st.plotly_chart(charts.long_run_chart(df), use_container_width=True)
st.plotly_chart(charts.monthly_chart(mdf), use_container_width=True)
if df["logged"].any():
    st.plotly_chart(charts.consistency_chart(df), use_container_width=True)
else:
    st.info("Log some sessions in the Weekly Planner to see consistency.")
display_df = df.rename(columns={
    "planned": "Planned running (km)",
    "adjusted": "Adjusted running (km)",
    "actual": "Actual running (km)",
    "completion": "Completion (%)",
    "long_planned": "Planned long run (km)",
    "long_actual": "Actual long run (km)",
})
st.dataframe(display_df.drop(columns=["logged"]), use_container_width=True, hide_index=True)