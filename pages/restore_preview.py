import streamlit as st

from services.restore_preview import preview_restore, RestorePreviewError
from utils.bootstrap import ready, get_app_user
from database.session import session_scope


def render_restore_preview():
    """Render the restore preview UI component."""
    ready()
    st.title("Restore Account Data")
    st.caption("Upload a JSON backup to preview what will be restored.")

    backup_file = st.file_uploader("JSON backup file", type="json")
    if not backup_file:
        st.stop()

    try:
        with session_scope() as s:
            user = get_app_user(s)
            preview = preview_restore(s, user, backup_file.getvalue().decode("utf-8"))

        st.subheader("Restore Preview")
        st.markdown(f"""This backup contains data for the account '{user.name}' (ID: {user.id}).

**Tables included:**
- {preview['users']} user record
- {preview['athlete_profile']} profile entry
- {preview['training_weeks']} training weeks
- {preview['running_sessions']} running sessions
- {preview['strength_sessions']} strength sessions
- {preview['strength_logs']} strength logs
- {preview['strength_set_details']} per-set details
- {preview['notes']} notes
- {preview['events']} events
- {preview['ai_adjustments']} AI adjustments
- {preview['settings']} settings
- {preview['run_correction_history']} run corrections

**Important:** This restore will replace only your current account's data. Other accounts, Garmin credentials, and external data remain untouched.

Click below to confirm and restore.""")
        if st.button("Confirm Restore"):
            try:
                with session_scope() as s:
                    counts = restore_preview.restore_with_preview(s, user, backup_file.getvalue().decode("utf-8"))
                    st.success(f"Restored {counts}")
            except Exception as exc:
                st.error(f"Restore failed: {exc}")
    except RestorePreviewError as exc:
        st.error(f"Invalid backup: {exc}")
    except Exception as exc:
        st.error(f"Error processing backup: {exc}")


def render_weekly_reflection():
    """Render optional weekly reflection prompt."""
    st.subheader("Weekly Reflection")
    st.markdown("Optional: Reflect on your week. This does not affect your plan or show streaks.")
    if st.button("Add reflection note"):
        st.session_state["show_reflection_form"] = True

    if st.session_state.get("show_reflection_form"):
        with st.form("reflection_form"):
            date = st.date_input("Date", value=None)
            category = st.selectbox("Category", ["Training", "Recovery", "Injury", "Other"])
            text = st.text_area("Reflection", placeholder="What went well? What could improve?")
            submitted = st.form_submit_button("Save reflection")
            if submitted and text.strip():
                try:
                    with session_scope() as s:
                        user = get_app_user(s)
                        ps.add_note(s, user, date or datetime.now().date(), category, text.strip())
                    st.success("Reflection saved.")
                    st.session_state.pop("show_reflection_form", None)
                except Exception as exc:
                    st.error(f"Could not save reflection: {exc}")