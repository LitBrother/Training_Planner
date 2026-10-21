from datetime import date
from uuid import uuid4

import streamlit as st
from garminconnect import (
    GarminConnectAuthenticationError, GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from database.session import session_scope
from services import ai_service as ai
from services import plan_service as ps
from services import coaching
from services.auth import runtime_config
from services.garmin_sync import fetch_runs_cached, garmin_credentials
from utils.bootstrap import get_app_user, get_openai_key, ready


def _capture_review_choices():
    identity = st.session_state.get("_ai_notes_consent_identity")
    st.session_state["_ai_notes_request_opt_in"] = bool(st.session_state.get("ai_notes_opt_in", False))
    st.session_state["_ai_garmin_request_opt_in"] = bool(
        st.session_state.get("ai_garmin_recovery_opt_in", False)
    )
    st.session_state["_ai_notes_request_identity"] = identity
    st.session_state["_ai_garmin_request_identity"] = identity
    st.session_state["ai_notes_opt_in"] = False
    st.session_state["ai_garmin_recovery_opt_in"] = False


ready()
st.title("🤖 AI Coach")
st.caption("Optional. AI recommendations are previewed first and apply only after you explicitly confirm. "
           "Proposals are validated with a 10% cap above planned "
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
    consent_identity = user.auth_uid or f"local-user-{user.id}"
    prior_identity = st.session_state.get("_ai_coach_account_identity")
    if prior_identity is not None and prior_identity != consent_identity:
        # Session-cached Garmin data and previews are account-bound; never carry them
        # into another athlete's review.
        st.session_state.pop("garmin_runs", None)
        st.session_state.pop("garmin_wellness", None)
        st.session_state.pop("_ai_plan_preview", None)
        st.session_state.pop("ai_confirmed_count", None)
    if st.session_state.get("_ai_notes_consent_identity") != consent_identity:
        st.session_state.pop("_ai_notes_request_opt_in", None)
        st.session_state.pop("_ai_notes_request_identity", None)
        st.session_state.pop("_ai_garmin_request_opt_in", None)
        st.session_state.pop("_ai_garmin_request_identity", None)
        st.session_state["ai_notes_opt_in"] = False
        st.session_state["ai_garmin_recovery_opt_in"] = False
        st.session_state["_ai_notes_consent_identity"] = consent_identity
    st.session_state["_ai_coach_account_identity"] = consent_identity
    cfg = ps.get_settings(s, user)
    runs = st.session_state.get("garmin_runs")
    wellness = st.session_state.get("garmin_wellness")
    hosted = runtime_config()[0] is not None
    if runs is None and st.button("Load Garmin data first"):
        with st.spinner("Contacting Garmin..."):
            try:
                if hosted:
                    from services.garmin_accounts import fetch_account_runs
                    st.session_state["garmin_runs"] = fetch_account_runs(s, user)
                    from services.garmin_accounts import fetch_account_wellness
                    st.session_state["garmin_wellness"] = fetch_account_wellness(s, user)
                else:
                    credentials = garmin_credentials()
                    st.session_state["garmin_runs"] = fetch_runs_cached(*credentials)
                    from services.garmin_sync import fetch_wellness
                    st.session_state["garmin_wellness"] = fetch_wellness(*credentials)
                st.rerun()
            except RuntimeError:
                st.error("Garmin sync failed. Check the Garmin connection in Settings and try again.")
            except (
                GarminConnectAuthenticationError, GarminConnectConnectionError,
                GarminConnectTooManyRequestsError,
            ):
                st.error("Garmin sync failed. Check your Garmin connection and try again.")
    st.caption(f"Garmin data: {len(runs)} runs loaded" if runs else "No Garmin runs loaded.")

    st.checkbox(
        "I agree to send my recent note text to Google Gemini for this review.",
        key="ai_notes_opt_in", disabled=not key,
    )
    if wellness:
        st.checkbox(
            "I agree to send my compact Garmin recovery metrics to Google Gemini for this review.",
            key="ai_garmin_recovery_opt_in", disabled=not key,
        )
    else:
        st.caption("Garmin recovery metrics are not loaded and will not be sent.")
    include_notes_notice = bool(st.session_state.get("ai_notes_opt_in", False)) or (
        bool(st.session_state.get("_ai_notes_request_opt_in", False))
        and st.session_state.get("_ai_notes_request_identity") == consent_identity
    )
    include_recovery_notice = (
        bool(st.session_state.get("ai_garmin_recovery_opt_in", False))
        or (
            bool(st.session_state.get("_ai_garmin_request_opt_in", False))
            and st.session_state.get("_ai_garmin_request_identity") == consent_identity
        )
    ) and bool(wellness)
    categories = [
        "training plan (upcoming sessions and recent weekly totals)",
        "goal and event details",
        "training focus and experience level",
        "recent strength-training history",
        "fixed research references",
    ]
    if runs:
        categories.append("compact recent Garmin running activity and derived training-load summary")
    if include_notes_notice:
        categories.append("recent notes (date, category, and text)")
    if include_recovery_notice:
        categories.append("compact Garmin recovery metrics")
    st.info(
        "Purpose: review your training and suggest safe, validated plan changes and strength guidance. "
        "Recipient/provider: Google Gemini. Categories sent for this review: "
        + "; ".join(categories)
        + ". Age and body weight are not sent. Notes and Garmin recovery data are omitted unless you "
          "check their separate boxes for this review; a saved recovery preference does not count as consent."
    )

    review_submitted = st.button(
        "Review my training", disabled=not key, on_click=_capture_review_choices
    )
    if review_submitted and key:
        request_identity = st.session_state.pop("_ai_notes_request_identity", None)
        include_notes = bool(st.session_state.pop("_ai_notes_request_opt_in", False))
        include_notes = include_notes and request_identity == consent_identity
        recovery_identity = st.session_state.pop("_ai_garmin_request_identity", None)
        include_garmin_recovery = bool(st.session_state.pop("_ai_garmin_request_opt_in", False))
        include_garmin_recovery = (
            include_garmin_recovery and recovery_identity == consent_identity and bool(wellness)
        )
        for result_key in (
            "ai_summary", "ai_errors", "ai_strength_advice", "ai_strength_errors",
            "ai_confirmed_count", "_ai_plan_preview",
        ):
            st.session_state.pop(result_key, None)
        try:
            with st.spinner("Reviewing..."):
                ctx = ai.build_context(
                    s, user, today, runs, wellness, include_notes=include_notes,
                    include_garmin_recovery=include_garmin_recovery,
                )
                summary, raw, strength_raw = ai.request_proposals(
                    ctx, key, cfg.openai_model, include_notes=include_notes,
                    include_garmin_recovery=include_garmin_recovery,
                )
                up = ai.upcoming_sessions(s, user, today)
                rule_raw = coaching.proposals_from_load(coaching.analyze(runs, today), up) if runs else []
                ok, errs, snapshot = ai.create_preview(rule_raw + raw, up, user.id)
                strength_ok, strength_errors = ai.validate_strength_advice(
                    strength_raw, ctx["strength_history"]
                )
            st.session_state["ai_summary"], st.session_state["ai_errors"] = summary, errs
            st.session_state["ai_strength_advice"] = strength_ok
            st.session_state["ai_strength_errors"] = strength_errors
            if ok:
                st.session_state["_ai_plan_preview"] = {
                    "preview_id": uuid4().hex,
                    "account_identity": consent_identity,
                    "user_id": user.id,
                    "proposals": [proposal.model_dump(mode="json") for proposal in ok],
                    "snapshot": snapshot,
                }
            else:
                st.info("No safe plan changes were proposed. Your plan was not changed.")
        except ai.AIRequestError:
            # Provider failures are sanitized by the service and cannot echo request data.
            st.error("AI request failed. Please try again or check your Gemini configuration.")
        except ValueError:
            st.error("AI review could not be prepared safely. Please retry the review.")
        finally:
            st.session_state.pop("_ai_notes_request_opt_in", None)
            st.session_state.pop("_ai_notes_request_identity", None)
            st.session_state.pop("_ai_garmin_request_opt_in", None)
            st.session_state.pop("_ai_garmin_request_identity", None)
    if st.session_state.get("ai_summary"):
        st.info(st.session_state["ai_summary"])
        if st.session_state.get("ai_confirmed_count"):
            st.success(f"Applied {st.session_state['ai_confirmed_count']} confirmed change(s).")
    for e in st.session_state.get("ai_errors", []):
        st.caption(f"Rejected by safety rules: {e}")
    for e in st.session_state.get("ai_strength_errors", []):
        st.caption(f"Strength guidance withheld: {e}")

    preview = st.session_state.get("_ai_plan_preview")
    if preview and (
        preview.get("account_identity") != consent_identity or preview.get("user_id") != user.id
    ):
        st.session_state.pop("_ai_plan_preview", None)
        preview = None
    if preview:
        st.subheader("Review proposed plan changes")
        st.caption("Nothing has changed yet. Select the exact proposals you want, then confirm.")
        selection = {}
        for index, item in enumerate(preview["proposals"]):
            session_date = date.fromisoformat(item["session_date"])
            before = preview["snapshot"]["sessions"][item["session_date"]]
            proposed_type = item.get("new_type") or before["type"]
            selection[index] = st.checkbox(
                f"Select {session_date:%d %b %Y}",
                key=f"ai_preview_{preview['preview_id']}_{index}",
                value=True,
            )
            st.text(
                f"{session_date:%d %b %Y} | {before['adjusted_km']:g} km → {item['new_km']:g} km | "
                f"{before['type']} → {proposed_type} | {item.get('reason', '')}"
            )
        confirm_col, cancel_col = st.columns(2)
        if confirm_col.button("Confirm selected changes", key="ai_confirm_plan_changes"):
            selected = [
                item for index, item in enumerate(preview["proposals"]) if selection[index]
            ]
            if not selected:
                st.warning("Select at least one reviewed proposal to confirm.")
            else:
                try:
                    count = ai.apply_reviewed_proposals(
                        s, user, preview["proposals"], selected, preview["snapshot"], today
                    )
                    st.session_state["ai_confirmed_count"] = count
                    st.session_state.pop("_ai_plan_preview", None)
                    st.success(f"Applied {count} confirmed change(s).")
                    rerun = True
                except ValueError:
                    st.session_state.pop("_ai_plan_preview", None)
                    st.error("The preview could not be applied safely. Review your current plan again.")
                    rerun = True
        if cancel_col.button("Cancel preview", key="ai_cancel_plan_preview"):
            st.session_state.pop("_ai_plan_preview", None)
            st.info("Preview canceled. Your plan was not changed.")
            rerun = True

    recommendations = st.session_state.get("ai_strength_advice", [])
    if recommendations:
        st.subheader("Strength guidance for your next sessions")
        sources = {source["id"]: source for source in ai.evidence.SOURCES}
        for item in recommendations:
            st.markdown(
                f"**{item['exercise']}** · {item['latest_weight_kg']:g} kg → "
                f"**{item['next_weight_kg']:g} kg**, target {item['target_reps']} reps"
            )
            st.write(item["reason"])
            st.caption("Only increase if you completed the target reps with good form and no pain.")
            for source_id in item["evidence_ids"]:
                source = sources.get(source_id)
                if source:
                    st.markdown(f"- **{source['principle']}**: [{source['citation']}]({source['url']})")
                else:
                    st.caption(f"Source {source_id} not found.")

    st.subheader("AI change history")
    if st.button("Clear filters", key="ai_history_clear_filters"):
        st.session_state["ai_history_start_date"] = None
        st.session_state["ai_history_end_date"] = None
        st.session_state["ai_history_status"] = "All"
        st.rerun()
    from_col, to_col, status_col = st.columns(3)
    with from_col:
        history_start = st.date_input(
            "From date", value=None, key="ai_history_start_date",
            help="Include changes created on or after this date.",
        )
    with to_col:
        history_end = st.date_input(
            "To date", value=None, key="ai_history_end_date",
            help="Include changes created on or before this date.",
        )
    with status_col:
        status_filter = st.selectbox(
            "Status",
            ["All", "Applied", "Approved", "Pending", "Rejected", "Reverted"],
            key="ai_history_status",
        )

    all_history = ps.list_adjustments(s, user)
    if history_start and history_end and history_start > history_end:
        st.error("From date must be on or before To date.")
        history = []
    else:
        history = ps.list_adjustments(
            s,
            user,
            status=None if status_filter == "All" else status_filter.lower(),
            start_date=history_start,
            end_date=history_end,
        )
    if not all_history:
        st.write("No change history yet.")
    elif not history:
        st.write("No changes match these filters.")
    for a in history:
        st.markdown(
            f"**{a.created_at:%d %b %Y} · {a.status.title()}** · "
            f"{a.session_date:%d %b %Y} session · "
            f"{a.old_km:g} → **{a.new_km:g} km** · "
            f"{(a.old_type or '—')} → {(a.new_type or a.old_type or '—')}"
        )
        st.caption(f"{a.kind.replace('_', ' ').title()} · {a.reason}")
        if a.status in {"approved", "applied"} and st.button(
            "Revert this change", key=f"revert-history-{a.id}"
        ):
            ai.set_status(s, user, a.id, "reverted")
            rerun = True
if rerun:
    st.rerun()