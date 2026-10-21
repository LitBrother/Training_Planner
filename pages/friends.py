from datetime import date

import streamlit as st
from sqlalchemy import select

from database.session import session_scope
from models.orm import RunningSession, User
from services import plan_service as ps
from services.adjustments import INTENSITY, rank
from utils.bootstrap import get_app_user, ready

ready()
st.title("👥 Friends & shared plans")
st.caption("Only accepted friends can view and directly edit each other's future training plan.")
with session_scope() as s:
    signed_in = bool(get_app_user(s).auth_uid)
if not signed_in:
    st.info("Friend connections need sign-in accounts. Enable Supabase Auth to invite people.")
    st.stop()

rerun = False
with session_scope() as s:
    user = get_app_user(s)
    with st.form("friend_request"):
        email = st.text_input("Invite by account email")
        send = st.form_submit_button("Send request")
    if send:
        try:
            ps.send_friend_request(s, user, email)
            st.success("Friend request sent.")
            rerun = True
        except ValueError as exc:
            st.error(str(exc))

    incoming, outgoing = ps.friend_requests(s, user)
    if incoming:
        st.subheader("Incoming requests")
        for request in incoming:
            other_id = request.requested_by
            other = s.get(User, other_id)
            c1, c2, c3 = st.columns([4, 1, 1])
            c1.write(f"**{other.name if other else 'Account'}** ({other.email if other else 'unknown email'})")
            if c2.button("Accept", key=f"accept{request.id}"):
                ps.respond_friend_request(s, user, request.id, True)
                rerun = True
            if c3.button("Decline", key=f"decline{request.id}"):
                ps.respond_friend_request(s, user, request.id, False)
                rerun = True
    if outgoing:
        st.caption(f"{len(outgoing)} outgoing request(s) waiting for acceptance.")

    friends = ps.accepted_friends(s, user)
    if not friends:
        st.info("Once you and a friend accept the connection, their plan will appear here.")
    else:
        friend = st.selectbox("Shared plan", friends, format_func=lambda item: f"{item.name} ({item.email})")
        friendship = ps.get_friendship(s, user.id, friend.id)
        
        # New UI logic
        if not (friendship.viewer or friendship.editor):
            st.info("You need to grant access permissions to see/edit their plan.")
            with st.form("permissions"):
                viewer = st.checkbox("Viewer (can see dates/types/distance)")
                editor = st.checkbox("Editor (can change dates/types/distance)")
                confirm = st.form_submit_button("Confirm permissions")
                if confirm:
                     ps.update_permissions(s, user.id, friend.id, viewer, editor)
                     st.success("Permissions updated.")
                     rerun = True
        else:
            ps.assert_friend_access(s, user, friend.id)
            cfg = ps.get_settings(s, friend)
            st.subheader(f"{friend.name}'s training goal")
            with st.form(f"goal{friend.id}"):
                has_race = st.checkbox("Goal race is set", value=cfg.race_date is not None)
                race_date = st.date_input("Goal race date", value=cfg.race_date or date.today()) if has_race else None
                save_goal = st.form_submit_button("Update goal")
            if save_goal:
                try:
                    ps.update_shared_goal(s, user, friend, race_date)
                    st.success("Goal and training plan updated.")
                    rerun = True
                except ValueError as exc:
                    st.error(f"Could not update goal: {exc}")

            weeks = ps.list_weeks(s, friend)
            if weeks:
                selected = st.selectbox(
                    "Training week", weeks, index=ps.current_week_index(weeks, date.today()),
                    format_func=lambda week: f"Week {week.week_no} | {week.week_start:%d %b %Y} | {week.phase}",
                    key=f"week{friend.id}",
                )
                runs = list(s.scalars(select(RunningSession).where(
                    RunningSession.user_id == friend.id, RunningSession.week_id == selected.id
                ).order_by(RunningSession.date)))
                st.write(f"**{selected.adjusted_km:g} adjusted km** of {selected.planned_km:g} planned km")
                for run in runs:
                    run_type = run.adjusted_type or run.run_type
                    st.markdown(f"**{run.date:%a %d %b} — {run.name}** · {run_type} · "
                                f"{run.adjusted_km:g} km (planned {run.planned_km:g} km)")
                    if run.status != "planned" or run.date < date.today() or run.slot == "race" or not friendship.editor:
                        st.caption("This session is not editable.")
                        continue
                    options = [name for name, intensity in INTENSITY.items() if intensity <= rank(run_type)]
                    with st.form(f"edit{friend.id}_{run.id}"):
                        c1, c2 = st.columns(2)
                        distance = c1.number_input(
                            "Distance (km)", min_value=0.0, max_value=max(run.planned_km * 1.1, 0.1),
                            value=float(run.adjusted_km), step=0.5, key=f"km{friend.id}_{run.id}",
                        )
                        kind = c2.selectbox(
                            "Workout type", options, index=options.index(run_type),
                            key=f"type{friend.id}_{run.id}",
                        )
                        save = st.form_submit_button("Save edit")
                    if save:
                        try:
                            ps.edit_shared_run(s, user, friend, run.id, distance, kind)
                            st.success("Shared plan updated.")
                            rerun = True
                        except ValueError as exc:
                            st.error(str(exc))

        if st.button(f"Disconnect from {friend.name}", type="secondary"):
            ps.remove_friend(s, user, friend.id)
            st.success("Friend connection removed.")
            rerun = True
if rerun:
    st.rerun()
