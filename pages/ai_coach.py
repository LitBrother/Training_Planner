from datetime import date

import streamlit as st

from database.session import session_scope
from services import ai_service as ai
from services import plan_service as ps
from services import coaching
from services.auth import runtime_config
from services.garmin_sync import fetch_runs_cached, garmin_credentials
from utils.bootstrap import get_app_user, get_openai_key, ready

ready()
st.title("🤖 AI Coach")
st.caption("Optional. Validated AI recommendations are applied automatically, with a 10% cap above planned "
           "daily and weekly distance and no increases above your peak long-run target. "
           "The AI cannot move sessions or increase training during taper or race week. "
           "This is not medical advice; see a professional for pain or injury.")
key = get_openai_key()
if not key:
    st.warning("No Gemini API key configured (Settings or .streamlit/secrets.toml). The rest of the app works without it.")
rerun = False
today = date.today()

with session_scope() as s:
    user = get_app_user(s)
    cfg = ps.get_settings(s, user)
    runs = st.session_state.get("garmin_runs")
    hosted = runtime_config()[0] is not None
    if runs is None and st.button("Load Garmin data first"):
        with st.spinner("Contacting Garmin..."):
            try:
                if hosted:
                    from services.garmin_accounts import fetch_account_runs
                    st.session_state["garmin_runs"] = fetch_account_runs(s, user)
                else:
                    st.session_state["garmin_runs"] = fetch_runs_cached(*garmin_credentials())
                st.rerun()
            except Exception as exc:
                st.error(f"Garmin sync failed: {exc}")
    st.caption(f"Garmin data: {len(runs)} runs loaded" if runs else "No Garmin data loaded, so the AI only sees the plan.")

    if st.button("Review my training", disabled=not key) and key:
        try:
            with st.spinner("Reviewing..."):
                ctx = ai.build_context(s, user, today, runs)
                summary, raw = ai.request_proposals(ctx, key, cfg.openai_model)
                up = ai.upcoming_sessions(s, user, today)
                rule_raw = coaching.proposals_from_load(coaching.analyze(runs, today), up) if runs else []
                ok, errs = ai.validate_proposals(rule_raw + raw, up)
                count = ai.save_proposals(s, user, ok, up)
            st.session_state["ai_summary"], st.session_state["ai_errors"] = summary, errs
            st.session_state["ai_applied_count"] = count
        except Exception as exc:
            st.error(f"AI request failed: {exc}")
    if st.session_state.get("ai_summary"):
        st.info(st.session_state["ai_summary"])
        st.success(f"Automatically applied {st.session_state.get('ai_applied_count', 0)} safe change(s).")
    for e in st.session_state.get("ai_errors", []):
        st.caption(f"Rejected by safety rules: {e}")

    st.subheader("Recent applied changes")
    applied = [a for a in ps.list_adjustments(s, user) if a.status == "applied"]
    if not applied:
        st.write("No applied changes yet.")
    for a in applied[:20]:
        st.markdown(f"**{a.session_date:%d %b %Y}** | {a.old_km:g} → **{a.new_km:g} km**"
                    f" | {a.old_type} → {a.new_type or a.old_type}")
        st.caption(a.reason)
        if st.button("Revert this change", key=f"revert{a.id}"):
            ai.set_status(s, user, a.id, "reverted")
            rerun = True
    prior = [a for a in ps.list_adjustments(s, user) if a.status == "approved"]
    if prior:
        st.subheader("Previously approved adjustments")
        for a in prior[:10]:
            st.write(f"{a.session_date:%d %b %Y}: {a.old_km:g} → {a.new_km:g} km")
            if st.button("Revert this change", key=f"revert-legacy{a.id}"):
                ai.set_status(s, user, a.id, "reverted")
                rerun = True
    legacy_pending = ps.list_adjustments(s, user, "pending")
    if legacy_pending:
        st.subheader("Older pending proposals")
        for a in legacy_pending:
            st.write(f"{a.session_date:%d %b}: {a.old_km:g} → {a.new_km:g} km")
            c1, c2 = st.columns(2)
            if c1.button("Apply", key=f"ap{a.id}"):
                ai.set_status(s, user, a.id, "approved")
                rerun = True
            if c2.button("Reject", key=f"rj{a.id}"):
                ai.set_status(s, user, a.id, "rejected")
                rerun = True
if rerun:
    st.rerun()