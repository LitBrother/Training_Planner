from datetime import date, timedelta

import streamlit as st

from database.session import session_scope
from services import plan_service as ps
from services.guidance import run_guidance, strength_guidance
from services.plan_generator import PHASE_GOALS
from utils.bootstrap import ready

ready()
st.title("🗓️ Weekly Planner")
st.caption("Workout order never changes automatically. Only volume and intensity are adjusted.")
rerun = False

with session_scope() as s:
    user = ps.ensure_user(s)
    weeks = ps.list_weeks(s, user)
    idx = st.selectbox("Week", range(len(weeks)), index=ps.current_week_index(weeks, date.today()),
                       format_func=lambda i: f"Week {weeks[i].week_no} | {weeks[i].week_start:%d %b %Y} | {weeks[i].phase}"
                       + (" (recovery)" if weeks[i].is_recovery else ""))
    w = weeks[idx]
    st.info(f"**{w.phase}**: {PHASE_GOALS[w.phase]}")
    m = st.columns(4)
    m[0].metric("Planned km", f"{w.planned_km:g}")
    m[1].metric("Adjusted km", f"{w.adjusted_km:g}")
    m[2].metric("Actual km", f"{w.actual_km:g}")
    m[3].metric("Completion", f"{w.completion_pct:.0f}%")
    runs = {r.date: r for r in ps.week_runs(s, w.id)}
    strength = {x.date: x for x in ps.week_strength(s, w.id)}
    events = {}
    for e in ps.list_events(s, user):
        events.setdefault(e.date, []).append(e)

    for i in range(7):
        d = w.week_start + timedelta(days=i)
        st.markdown(f"### {d:%A %d %b}")
        for e in events.get(d, []):
            st.warning(f"🏁 Event: {e.name} ({e.distance_km:g} km, priority {e.priority})")
        r = runs.get(d)
        if r:
            typ = r.adjusted_type or r.run_type
            changed = abs(r.adjusted_km - r.planned_km) > 0.05
            st.markdown(f"**🏃 {r.name}**: {typ} | planned {r.planned_km:g} km | "
                        f"**adjusted {r.adjusted_km:g} km**{' ⚠️' if changed else ''} | status: {r.status}")
            g = run_guidance(typ, r.variant, r.adjusted_km)
            with st.expander("Workout guidance"):
                for label, key in [("Purpose", "purpose"), ("Warm up", "warm_up"), ("Main set", "main_set"),
                                   ("Recovery", "recovery"), ("Cool down", "cool_down"), ("Target RPE", "rpe"),
                                   ("Talk test", "talk_test"), ("Common errors", "common_errors"),
                                   ("When to reduce", "reduce_when"), ("When to stop", "stop_when")]:
                    st.markdown(f"**{label}:** {g[key]}")
            with st.form(f"run{r.id}"):
                cols = st.columns([1, 1, 2, 1])
                status = cols[0].selectbox("Status", ["planned", "completed", "missed"],
                                           index=["planned", "completed", "missed"].index(r.status))
                actual = cols[1].number_input("Actual km", 0.0, 100.0, float(r.actual_km if r.actual_km else r.adjusted_km), 0.1)
                notes = cols[2].text_input("Notes", r.notes or "")
                if cols[3].form_submit_button("Save run"):
                    ps.log_run(s, user, r.id, status, actual, notes)
                    rerun = True
        x = strength.get(d)
        if x:
            sg = strength_guidance(x.kind, x.volume_level, x.core_minutes)
            st.markdown(f"**🏋️ {x.kind}**" + (f" (volume: {x.volume_level})" if x.kind == "Legs" else ""))
            with st.expander("Strength guidance"):
                st.write(sg["purpose"])
                for b in sg["bullets"]:
                    st.markdown(f"- {b}")
                if sg["core"]:
                    st.write(sg["core"])
            with st.form(f"str{x.id}"):
                c1, c2, c3 = st.columns([1, 3, 1])
                done = c1.checkbox("Completed", x.completed)
                notes = c2.text_input("Notes", x.notes or "", key=f"sn{x.id}")
                if c3.form_submit_button("Save strength"):
                    ps.log_strength(s, x.id, done, notes)
                    rerun = True
        if not r and not x:
            st.caption("Rest day")

if rerun:
    st.rerun()