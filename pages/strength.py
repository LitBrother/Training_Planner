from datetime import date

import plotly.express as px
import streamlit as st

from database.session import session_scope
from services import coaching
from services import strength_progress as sp
from utils.bootstrap import get_app_user, ready


ready()
st.title("🏋️ Strength")
st.page_link("pages/ai_coach.py", label="Review my training for AI-guided strength progression", icon="🤖")
st.caption(
    "This page tracks individual exercise-level lift logs; scheduled strength sessions are shown separately "
    "in the Weekly Planner."
)
st.page_link("pages/training_plan.py", label="View scheduled strength sessions in the Weekly Planner", icon="🗓️")

message = st.session_state.pop("strength_log_message", None)
if message:
    st.success(message)

st.subheader("Lift progression")
rerun = False
with session_scope() as s:
    user = get_app_user(s)
    st.caption(
        "Aggregate entry: records here store one weight and reps-per-set summary. "
        "Older aggregate logs remain unchanged and are not converted into per-set history."
    )
    with st.form("lift"):
        c = st.columns([3, 2, 1, 1, 2])
        ex = c[0].text_input("Exercise", placeholder="Bench Press")
        wt = c[1].number_input("Weight (kg)", 0.0, 500.0, 20.0, 2.5)
        sets = c[2].number_input("Sets", 1, 10, 3)
        reps = c[3].number_input("Reps", 1, 50, 8)
        when = c[4].date_input("Date", date.today())
        add_submitted = st.form_submit_button("Log lift")
    if add_submitted:
        try:
            sp.add_log(s, user, when, ex, wt, int(sets), int(reps))
            st.session_state["strength_log_message"] = "Lift logged."
            rerun = True
        except ValueError as exc:
            st.error(str(exc))

    st.subheader("Detailed lift (per set)")
    st.caption(
        "Metric only. Add 1–10 sets with 1–50 reps and 0–500 kg in 0.1 kg increments."
    )
    detail_count_key = "strength_detail_set_count"
    st.session_state.setdefault(detail_count_key, 1)
    detail_revision_key = "strength_detail_revision"
    st.session_state.setdefault(detail_revision_key, 0)
    detail_prefix = f"strength_detail_{st.session_state[detail_revision_key]}"
    detail_exercise = st.text_input(
        "Detailed exercise", key=f"{detail_prefix}_exercise"
    )
    detail_date = st.date_input(
        "Detailed date", date.today(), key=f"{detail_prefix}_date"
    )
    detail_count = st.session_state[detail_count_key]
    set_action_cols = st.columns(2)
    if set_action_cols[0].button(
        "Add set", key="strength_detail_add_set", disabled=detail_count >= 10
    ):
        st.session_state[detail_count_key] = detail_count + 1
    if set_action_cols[1].button(
        "Remove last set", key="strength_detail_remove_set", disabled=detail_count <= 1
    ):
        st.session_state[detail_count_key] = detail_count - 1
        st.session_state.pop(f"{detail_prefix}_weight_{detail_count - 1}", None)
        st.session_state.pop(f"{detail_prefix}_reps_{detail_count - 1}", None)
    detail_count = st.session_state[detail_count_key]
    for set_index in range(detail_count):
        st.markdown(f"**Set {set_index + 1}**")
        detail_cols = st.columns(2)
        detail_cols[0].number_input(
            f"Set {set_index + 1} weight (kg)", min_value=0.0, max_value=500.0,
            value=20.0, step=0.1, format="%.1f",
            key=f"{detail_prefix}_weight_{set_index}",
        )
        detail_cols[1].number_input(
            f"Set {set_index + 1} reps", min_value=1, max_value=50, value=8, step=1,
            key=f"{detail_prefix}_reps_{set_index}",
        )
    if st.button("Save detailed lift", key="save_detailed_strength_log"):
        rows = [
            {
                "weight_kg": st.session_state[f"{detail_prefix}_weight_{index}"],
                "reps": st.session_state[f"{detail_prefix}_reps_{index}"],
            }
            for index in range(detail_count)
        ]
        try:
            sp.add_detailed_log(s, user, detail_date, detail_exercise, rows)
            st.session_state["strength_log_message"] = "Detailed lift logged."
            st.session_state[detail_revision_key] += 1
            st.session_state[detail_count_key] = 1
            rerun = True
        except ValueError as exc:
            st.error(str(exc))

    logs = sp.list_logs(s, user)
    detailed_logs = {
        log.id: detail_rows for log, detail_rows in sp.list_detailed_logs(s, user)
    }
    if not logs:
        st.info("No strength logs yet. Log a lift above to start tracking progression.")
    else:
        for log in logs:
            row = st.columns([1.2, 2.5, 2.2, 1, 1])
            row[0].write(log.date.strftime("%d %b %Y"))
            row[1].write(log.exercise)
            detail_rows = detailed_logs.get(log.id)
            record_kind = "Detailed" if detail_rows else "Aggregate"
            row[2].write(
                f"{record_kind}: {log.weight_kg:g} kg · {log.sets} sets · {log.reps} reps"
            )
            if detail_rows:
                st.caption("Per-set detail")
                for detail in detail_rows:
                    st.write(
                        f"Set {detail.set_number}: {detail.weight_kg:g} kg × {detail.reps} reps"
                    )
            if row[3].button(
                "Edit sets" if detail_rows else "Edit",
                key=f"edit_strength_log_{log.id}",
            ):
                if detail_rows:
                    st.session_state[f"strength_detail_edit_count_{log.id}"] = len(detail_rows)
                    st.session_state[f"strength_detail_edit_exercise_{log.id}"] = log.exercise
                    st.session_state[f"strength_detail_edit_date_{log.id}"] = log.date
                    for detail in detail_rows:
                        st.session_state[
                            f"strength_detail_edit_weight_{log.id}_{detail.set_number - 1}"
                        ] = float(detail.weight_kg)
                        st.session_state[
                            f"strength_detail_edit_reps_{log.id}_{detail.set_number - 1}"
                        ] = int(detail.reps)
                revision_key = f"strength_edit_revision_{log.id}"
                st.session_state[revision_key] = st.session_state.get(revision_key, 0) + 1
                st.session_state["editing_strength_log"] = log.id
            if row[4].button("Delete", key=f"delete_strength_log_{log.id}"):
                st.session_state["confirm_strength_delete"] = log.id

            if st.session_state.get("editing_strength_log") == log.id:
                if detail_rows:
                    edit_count_key = f"strength_detail_edit_count_{log.id}"
                    edit_count = st.session_state[edit_count_key]
                    edit_cols = st.columns(2)
                    edit_exercise = edit_cols[0].text_input(
                        "Detailed exercise", key=f"strength_detail_edit_exercise_{log.id}"
                    )
                    edit_date = edit_cols[1].date_input(
                        "Detailed date", key=f"strength_detail_edit_date_{log.id}"
                    )
                    edit_actions = st.columns(2)
                    if edit_actions[0].button(
                        "Add set", key=f"strength_detail_edit_add_{log.id}",
                        disabled=edit_count >= 10,
                    ):
                        st.session_state[edit_count_key] = edit_count + 1
                        st.session_state[
                            f"strength_detail_edit_weight_{log.id}_{edit_count}"
                        ] = 20.0
                        st.session_state[
                            f"strength_detail_edit_reps_{log.id}_{edit_count}"
                        ] = 8
                    if edit_actions[1].button(
                        "Remove last set", key=f"strength_detail_edit_remove_{log.id}",
                        disabled=edit_count <= 1,
                    ):
                        st.session_state[edit_count_key] = edit_count - 1
                        st.session_state.pop(
                            f"strength_detail_edit_weight_{log.id}_{edit_count - 1}", None
                        )
                        st.session_state.pop(
                            f"strength_detail_edit_reps_{log.id}_{edit_count - 1}", None
                        )
                    edit_count = st.session_state[edit_count_key]
                    for set_index in range(edit_count):
                        st.markdown(f"**Set {set_index + 1}**")
                        edit_set_cols = st.columns(2)
                        edit_set_cols[0].number_input(
                            f"Set {set_index + 1} weight (kg)", 0.0, 500.0,
                            step=0.1, format="%.1f",
                            key=f"strength_detail_edit_weight_{log.id}_{set_index}",
                        )
                        edit_set_cols[1].number_input(
                            f"Set {set_index + 1} reps", 1, 50, step=1,
                            key=f"strength_detail_edit_reps_{log.id}_{set_index}",
                        )
                    save_edit = st.button(
                        "Save detailed changes", key=f"save_strength_detail_{log.id}"
                    )
                    cancel_edit = st.button(
                        "Cancel detailed edit", key=f"cancel_strength_detail_{log.id}"
                    )
                    if save_edit:
                        edit_rows = [
                            {
                                "weight_kg": st.session_state[
                                    f"strength_detail_edit_weight_{log.id}_{index}"
                                ],
                                "reps": st.session_state[
                                    f"strength_detail_edit_reps_{log.id}_{index}"
                                ],
                            }
                            for index in range(edit_count)
                        ]
                        try:
                            sp.update_detailed_log(
                                s, user, log.id, edit_date, edit_exercise, edit_rows
                            )
                            st.session_state["editing_strength_log"] = None
                            st.session_state["selected_strength_exercise"] = edit_exercise.strip().title()
                            st.session_state["strength_log_message"] = "Detailed lift updated."
                            rerun = True
                        except ValueError as exc:
                            st.error(str(exc))
                    elif cancel_edit:
                        st.session_state["editing_strength_log"] = None
                else:
                    revision = st.session_state.get(f"strength_edit_revision_{log.id}", 0)
                    with st.form(f"edit_strength_form_{log.id}"):
                        edit_cols = st.columns([2, 2, 1, 1, 2])
                        edit_exercise = edit_cols[0].text_input(
                            "Exercise", log.exercise, key=f"edit_strength_exercise_{log.id}_{revision}"
                        )
                        edit_weight = edit_cols[1].number_input(
                            "Weight (kg)", 0.0, 500.0, float(log.weight_kg), 2.5,
                            key=f"edit_strength_weight_{log.id}_{revision}",
                        )
                        edit_sets = edit_cols[2].number_input(
                            "Sets", 1, 10, int(log.sets),
                            key=f"edit_strength_sets_{log.id}_{revision}",
                        )
                        edit_reps = edit_cols[3].number_input(
                            "Reps", 1, 50, int(log.reps),
                            key=f"edit_strength_reps_{log.id}_{revision}",
                        )
                        edit_date = edit_cols[4].date_input(
                            "Date", log.date, key=f"edit_strength_date_{log.id}_{revision}"
                        )
                        save_col, cancel_col = st.columns(2)
                        save_edit = save_col.form_submit_button("Save changes")
                        cancel_edit = cancel_col.form_submit_button("Cancel")
                    if save_edit:
                        try:
                            sp.update_log(
                                s, user, log.id, edit_date, edit_exercise, edit_weight,
                                int(edit_sets), int(edit_reps),
                            )
                            st.session_state["editing_strength_log"] = None
                            st.session_state["selected_strength_exercise"] = edit_exercise.strip().title()
                            st.session_state["strength_log_message"] = "Strength log updated."
                            rerun = True
                        except ValueError as exc:
                            st.error(str(exc))
                    elif cancel_edit:
                        st.session_state["editing_strength_log"] = None

            if st.session_state.get("confirm_strength_delete") == log.id:
                st.warning(
                    f"Permanently delete {log.exercise} from {log.date:%d %b %Y}?"
                )
                confirm_col, cancel_col = st.columns(2)
                confirm_delete = confirm_col.button(
                    "Confirm delete", key=f"confirm_strength_delete_{log.id}"
                )
                cancel_delete = cancel_col.button(
                    "Cancel deletion", key=f"cancel_strength_delete_{log.id}"
                )
                if confirm_delete:
                    try:
                        sp.delete_log(s, user, log.id)
                        st.session_state.pop("confirm_strength_delete", None)
                        st.session_state["editing_strength_log"] = None
                        st.session_state["strength_log_message"] = "Strength log deleted."
                        rerun = True
                    except ValueError as exc:
                        st.error(str(exc))
                elif cancel_delete:
                    st.session_state.pop("confirm_strength_delete", None)

    df = sp.log_frame(s, user)

if rerun:
    st.rerun()

if not df.empty:
    exercises = sorted(df["exercise"].unique())
    current_pick = st.session_state.get("selected_strength_exercise")
    if current_pick not in exercises:
        st.session_state["selected_strength_exercise"] = exercises[0]
    pick = st.selectbox("Exercise", exercises, key="selected_strength_exercise")
    if pick:
        runs = st.session_state.get("garmin_runs") or []
        acwr = coaching.analyze(runs, date.today())["acwr"] if runs else None
        hold = bool(acwr and acwr > 1.3)
        st.success(sp.suggest_next(df, str(pick), hold))
        st.caption(
            "Estimated 1RM uses the Epley formula. For detailed lifts, it uses reps "
            "from the highest-weight set; ties use the set with the most reps."
        )
        st.caption(
            "No separately recorded tested 1RM is available. Logged lifts are estimates, "
            "not verified tests."
        )
        d = df[df["exercise"] == pick]
        fig = px.line(d, x="date", y="est_1rm", markers=True, template="plotly_dark",
                      title=f"{pick}: estimated 1RM (kg)", color_discrete_sequence=["#00D4AA"])
        fig.update_yaxes(title_text="Estimated 1RM (kg)")
        st.plotly_chart(fig, use_container_width=True)
        best = df.groupby("exercise")["est_1rm"].max().reset_index()
        best["Best estimated 1RM (kg)"] = best["est_1rm"]
        st.dataframe(best[["exercise", "Best estimated 1RM (kg)"]], hide_index=True)
