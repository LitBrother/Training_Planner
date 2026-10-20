import math
from datetime import date, timedelta, timezone

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from database.session import session_scope
from services import plan_service as ps
from services.guidance import run_guidance, strength_guidance
from services.plan_generator import PHASE_GOALS
from utils.bootstrap import get_app_user, ready


def _persist_training_pause(paused: bool) -> bool:
    try:
        with session_scope() as s:
            user = get_app_user(s)
            ps.set_training_paused(s, user, paused)
    except SQLAlchemyError:
        return False
    return True


ready()
st.title("🗓️ Weekly Planner")
with session_scope() as s:
    user = get_app_user(s)
    training_paused = ps.training_is_paused(s, user)

pause_identity = ("auth", user.auth_uid) if user.auth_uid else ("user", user.id)
if st.session_state.get("_training_pause_identity") != pause_identity:
    st.session_state.pop("training_pause_notice", None)
    st.session_state.pop("training_pause_confirmation", None)
    st.session_state["_training_pause_identity"] = pause_identity

notice = st.session_state.pop("training_pause_notice", None)
if notice:
    st.success(notice)

confirmation = st.session_state.get("training_pause_confirmation")
if (training_paused and confirmation == "pause") or (not training_paused and confirmation == "resume"):
    st.session_state["training_pause_confirmation"] = None

if training_paused:
    st.warning(
        "Training is paused. Calendar dates do not shift. Sessions that pass continue to follow "
        "the existing missed-session rules; no missed sessions are moved or made up."
    )
    if st.button("Resume training", key="resume-training"):
        st.session_state["training_pause_confirmation"] = "resume"
    if st.session_state.get("training_pause_confirmation") == "resume":
        st.info(
            "Resuming only clears the pause status. Scheduled dates will not shift, and no "
            "missed sessions will be moved or made up."
        )
        with st.form("confirm-resume-training"):
            confirm_resume = st.form_submit_button("Confirm resume training")
            cancel_resume = st.form_submit_button("Cancel")
        if confirm_resume or cancel_resume:
            st.session_state["training_pause_confirmation"] = None
            if cancel_resume:
                st.rerun()
            elif _persist_training_pause(False):
                st.session_state["training_pause_notice"] = (
                    "Training resumed. Scheduled dates remain unchanged; no catch-up sessions were added."
                )
                st.rerun()
            else:
                st.error("Could not resume training. Your saved training status was not changed. Please try again.")
else:
    st.success("Training is active.")
    if st.button("Pause training", key="pause-training"):
        st.session_state["training_pause_confirmation"] = "pause"
    if st.session_state.get("training_pause_confirmation") == "pause":
        st.info(
            "Pausing does not shift calendar dates. Sessions that pass follow the existing missed-session "
            "rules, and no missed sessions are moved or made up."
        )
        with st.form("confirm-pause-training"):
            confirm_pause = st.form_submit_button("Confirm pause training")
            cancel_pause = st.form_submit_button("Cancel")
        if confirm_pause or cancel_pause:
            st.session_state["training_pause_confirmation"] = None
            if cancel_pause:
                st.rerun()
            elif _persist_training_pause(True):
                st.session_state["training_pause_notice"] = (
                    "Training paused. Dates remain unchanged; missed sessions follow the existing rules, "
                    "with no catch-up sessions added."
                )
                st.rerun()
            else:
                st.error("Could not pause training. Your saved training status was not changed. Please try again.")

st.caption("Workout order never changes automatically. Only volume and intensity are adjusted.")
rerun = False

with session_scope() as s:
    user = get_app_user(s)
    weeks = ps.list_weeks(s, user)
    idx = st.selectbox("Week", range(len(weeks)), index=ps.current_week_index(weeks, date.today()),
                       format_func=lambda i: f"Week {weeks[i].week_no} | {weeks[i].week_start:%d %b %Y} | {weeks[i].phase}"
                       + (" (recovery)" if weeks[i].is_recovery else ""))
    w = weeks[idx]
    st.info(f"**{w.phase}**: {PHASE_GOALS[w.phase]}")
    activity = ps.weekly_activity_summary(s, user, w)
    st.subheader("Weekly activity summary")
    if activity["has_running_records"]:
        m = st.columns(4)
        m[0].metric("Planned running (km)", f'{activity["planned_running_km"]:g}')
        m[1].metric("Adjusted running (km)", f'{activity["adjusted_running_km"]:g}')
        m[2].metric(
            "Actual running (km)",
            f'{activity["actual_running_km"]:g}' if activity["running_completion_pct"] is not None
            else "Not recorded",
        )
        completion = activity["running_completion_pct"]
        m[3].metric("Running completion (%)", f"{completion:.0f}%" if completion is not None else "Not recorded")
        st.caption(f'Run status: {activity["completed_runs"]} completed, {activity["missed_runs"]} missed, '
                   f'{activity["planned_runs"]} still planned.')
        st.caption("Running completion = Actual ÷ Adjusted (capped at 100%; 0% when Adjusted is 0).")
    else:
        st.info("No running-session records for this week; running totals are not recorded.")

    st.caption(
        "Scheduled-session counts come from the weekly plan; lift-log entries count exercise-level records "
        "from the Strength page. Lift-log entries are not added to or included in either scheduled-session count."
    )
    strength_metrics = st.columns(3)
    strength_metrics[0].metric("Planned strength sessions", activity["planned_strength_sessions"])
    strength_metrics[1].metric(
        "Completed planned strength sessions", activity["completed_strength_sessions"]
    )
    strength_metrics[2].metric("Lift-log entries", activity["lift_log_entries"])

    runs = {r.date: r for r in ps.week_runs(s, w.id, user)}
    strength = {x.date: x for x in ps.week_strength(s, w.id, user)}
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
            st.caption(
                "Marking a run missed keeps it on its scheduled day—no catch-up run is moved or added. "
                "It records 0 km actual, which can lower running completion and affect later running volume. "
                "After a missed long run, the next long run is capped at the missed run's planned distance, "
                "but other adjustments may make it shorter."
            )
            with st.form(f"run{r.id}"):
                cols = st.columns([1, 1, 2, 1])
                status = cols[0].selectbox("Status", ["planned", "completed", "missed"],
                                           index=["planned", "completed", "missed"].index(r.status))
                correction_only = (
                    r.status == "completed" and r.source in (None, "manual")
                    and r.garmin_id is None
                )
                actual = cols[1].number_input(
                    "Actual km", 0.0, 100.0,
                    float(r.actual_km if r.actual_km is not None else r.adjusted_km), 0.1,
                    disabled=correction_only,
                    help=("Use the separate correction form below to change this saved distance."
                          if correction_only else None),
                )
                notes = cols[2].text_input("Notes", r.notes or "")
                context = st.columns([1, 1])
                duration_text = context[0].text_input(
                    "Manual duration (minutes, optional)",
                    "" if r.manual_duration_min is None else f"{r.manual_duration_min:g}",
                    key=f"manual-duration-{r.id}",
                )
                rpe_options = ["Not recorded", *range(1, 11)]
                current_rpe = r.perceived_effort_rpe
                rpe_index = rpe_options.index(current_rpe) if current_rpe in rpe_options else 0
                perceived_effort = context[1].selectbox(
                    "Self-reported RPE (optional)", rpe_options, index=rpe_index,
                    key=f"manual-rpe-{r.id}",
                )
                st.caption(
                    "Self-reported RPE uses the conventional 1–10 scale: 1 is very easy and 10 is maximal. "
                    "It is subjective training feedback, not medical status, and is separate from prescribed Target RPE."
                )
                if cols[3].form_submit_button("Save run"):
                    try:
                        duration = float(duration_text) if duration_text.strip() else None
                        if duration is not None and (not math.isfinite(duration) or duration <= 0):
                            raise ValueError
                    except ValueError:
                        st.error("Manual duration must be a positive finite number of minutes.")
                    else:
                        ps.log_run(
                            s, user, r.id, status, actual, notes,
                            manual_duration_min=duration,
                            perceived_effort_rpe=None if perceived_effort == "Not recorded" else perceived_effort,
                        )
                        rerun = True
            correction_eligible = (
                r.status == "completed" and r.source in (None, "manual")
                and r.garmin_id is None
            )
            correction_value_valid = (
                isinstance(r.actual_km, (int, float))
                and math.isfinite(r.actual_km)
                and 0 < r.actual_km <= 100
            )
            if correction_eligible and correction_value_valid:
                with st.expander("Correct actual distance"):
                    st.caption(f"Saved actual distance: {r.actual_km:g} km")
                    with st.form(f"correct-run-{r.id}"):
                        corrected_km = st.number_input(
                            "Correct actual distance (km)", min_value=0.1, max_value=100.0,
                            value=float(r.actual_km), step=0.1,
                            help="Only the saved actual distance will change. This correction is recorded in your history.",
                        )
                        save_correction = st.form_submit_button("Save correction")
                        st.form_submit_button("Cancel")
                        if save_correction:
                            try:
                                changed = ps.correct_run_distance(
                                    s, user, r.id, "actual_km", corrected_km
                                )
                            except ValueError as exc:
                                st.error(str(exc))
                            else:
                                if changed:
                                    rerun = True
                                else:
                                    st.info("The saved distance is unchanged; no correction was recorded.")
            elif correction_eligible:
                st.caption(
                    "This completed run has no saved actual distance within the supported correction bounds."
                )
            history = ps.run_correction_history(s, user, r.id)
            if history:
                with st.expander("Actual distance correction history"):
                    for correction in reversed(history):
                        stamp = correction.corrected_at
                        if stamp.tzinfo is None:
                            stamp = stamp.replace(tzinfo=timezone.utc)
                        st.write(
                            f"{stamp:%Y-%m-%d %H:%M UTC} · "
                            f"{correction.old_value:g} km → {correction.new_value:g} km"
                        )
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
                    ps.log_strength(s, user, x.id, done, notes)
                    rerun = True
        if not r and not x:
            st.caption("Rest day")

if rerun:
    st.rerun()