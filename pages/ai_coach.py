from datetime import date

import streamlit as st

from database.session import session_scope
from services import ai_service as ai
from services import plan_service as ps
from services import coaching
from services.garmin_sync import fetch_runs_cached, garmin_credentials
from utils.bootstrap import get_openai_key, ready

ready()
st.title("🤖 AI Coach")
st.caption("Optional. The AI may only reduce volume, simplify quality sessions or add recovery. "
           "It can never add mileage, move sessions or remove the taper. Every change needs your approval. "
           "This is not medical advice; see a professional for pain or injury.")
key = get_openai_key()
if not key:
    st.warning("No OpenAI API key configured (Settings, secrets or OPENAI_API_KEY). The rest of the app works without it.")
    st.warning("No Gemini API key configured (Settings or .streamlit/secrets.toml). The rest of the app works without it.")
rerun = False
today = date.today()

with session_scope() as s:
    user = ps.ensure_user(s)
    cfg = ps.get_settings(s, user)
    runs = st.session_state.get("garmin_runs")
    if runs is None and st.button("Load Garmin data first"):
        with st.spinner("Contacting Garmin..."):
            st.session_state["garmin_runs"] = fetch_runs_cached(*garmin_credentials())
        st.rerun()
    st.caption(f"Garmin data: {len(runs)} runs loaded" if runs else "No Garmin data loaded, so the AI only sees the plan.")

    if st.button("Review my training", disabled=not key) and key:
        try:
            with st.spinner("Reviewing..."):
                ctx = ai.build_context(s, user, today, runs)
                summary, raw = ai.request_proposals(ctx, key, cfg.openai_model)
                up = ai.upcoming_sessions(s, user, today)
                rule_raw = coaching.proposals_from_load(coaching.analyze(runs, today), up) if runs else []
                ok, errs = ai.validate_proposals(rule_raw + raw, up)
                ai.save_proposals(s, user, ok, up)
            st.session_state["ai_summary"], st.session_state["ai_errors"] = summary, errs
        except Exception as exc:
            st.error(f"AI request failed: {exc}")
    if st.session_state.get("ai_summary"):
        st.info(st.session_state["ai_summary"])
    for e in st.session_state.get("ai_errors", []):
        st.caption(f"Rejected by safety rules: {e}")

    st.subheader("Pending proposals")
    pending = ps.list_adjustments(s, user, "pending")
    if not pending:
        st.write("None.")
    for a in pending:
        st.markdown(f"**{a.session_date:%a %d %b}** | {a.kind} | {a.old_km:g} → **{a.new_km:g} km**"
                    f" | {a.old_type} → {a.new_type or a.old_type}")
        st.caption(a.reason)
        c1, c2, _ = st.columns([1, 1, 6])
        if c1.button("Approve", key=f"ap{a.id}"):
            ai.set_status(s, user, a.id, "approved")
            rerun = True
        if c2.button("Reject", key=f"rj{a.id}"):
            ai.set_status(s, user, a.id, "rejected")
            rerun = True
    st.subheader("History")
    for a in ps.list_adjustments(s, user):
        if a.status != "pending":
            st.write(f"{a.session_date:%d %b %Y}: {a.old_km:g} → {a.new_km:g} km ({a.status}): {a.reason}")
if rerun:
    st.rerun()