import os
import time
from typing import Any

import requests
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

SESSION_KEY = "planner_auth"
AUTH_TIMEOUT = 15


class AuthError(RuntimeError):
    pass


def _setting(key: str) -> str:
    try:
        value = st.secrets.get(key, "")
    except StreamlitSecretNotFoundError:
        value = ""
    return str(value or os.getenv(key, "")).strip()


def runtime_config() -> tuple[str | None, str | None, str | None]:
    database_url = _setting("DATABASE_URL")
    supabase_url = _setting("SUPABASE_URL").rstrip("/")
    anon_key = _setting("SUPABASE_ANON_KEY")
    configured = (database_url, supabase_url, anon_key)
    if not any(configured):
        return None, None, None
    if not all(configured):
        missing = [
            key for key, value in zip(
                ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_ANON_KEY"), configured
            ) if not value
        ]
        raise AuthError(f"Hosted setup is incomplete. Configure: {', '.join(missing)}.")
    return database_url, supabase_url, anon_key


def _request(path: str, payload: dict[str, Any], *, api_key: str,
             access_token: str | None = None) -> dict[str, Any]:
    headers = {"apikey": api_key, "Content-Type": "application/json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    try:
        response = requests.post(
            f"{_setting('SUPABASE_URL').rstrip('/')}/auth/v1/{path}",
            json=payload, headers=headers, timeout=AUTH_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise AuthError("Could not reach the account service. Check your connection and try again.") from exc
    try:
        result = response.json()
    except ValueError:
        result = {}
    if not response.ok:
        message = result.get("msg") or result.get("message") or result.get("error_description")
        if not message:
            message = "Account request failed. Check the account details and try again."
        raise AuthError(str(message))
    return result


def _store_session(data: dict[str, Any]) -> None:
    user = data.get("user") or {}
    if not data.get("access_token") or not user.get("id"):
        raise AuthError("The account service did not return a usable sign-in session.")
    st.session_state[SESSION_KEY] = {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", ""),
        "expires_at": time.time() + int(data.get("expires_in", 3600)),
        "user_id": str(user["id"]),
        "email": str(user.get("email", "")),
    }


def sign_in(email: str, password: str, api_key: str) -> None:
    _store_session(_request(
        "token?grant_type=password", {"email": email, "password": password}, api_key=api_key
    ))


def sign_up(email: str, password: str, api_key: str) -> bool:
    data = _request("signup", {"email": email, "password": password}, api_key=api_key)
    if data.get("access_token") and (data.get("user") or {}).get("id"):
        _store_session(data)
        return True
    return False


def sign_out(api_key: str) -> None:
    session = st.session_state.pop(SESSION_KEY, None)
    if not session or not session.get("access_token"):
        return
    try:
        _request("logout", {}, api_key=api_key, access_token=session["access_token"])
    except AuthError as exc:
        st.session_state["auth_notice"] = f"Signed out locally; server sign-out failed: {exc}"


def current_identity(api_key: str) -> tuple[str, str] | None:
    session = st.session_state.get(SESSION_KEY)
    if not session:
        return None
    if session["expires_at"] <= time.time() + 30:
        refresh_token = session.get("refresh_token")
        if not refresh_token:
            st.session_state.pop(SESSION_KEY, None)
            return None
        try:
            refreshed = _request(
                "token?grant_type=refresh_token",
                {"refresh_token": refresh_token}, api_key=api_key,
            )
            _store_session(refreshed)
            session = st.session_state[SESSION_KEY]
        except AuthError as exc:
            st.session_state.pop(SESSION_KEY, None)
            st.session_state["auth_notice"] = str(exc)
            return None
    return str(session["user_id"]), str(session["email"])


def render_login(api_key: str) -> bool:
    identity = current_identity(api_key)
    if identity:
        st.sidebar.caption(f"Signed in as {identity[1]}")
        if st.sidebar.button("Sign out"):
            sign_out(api_key)
            st.rerun()
        return True

    st.title("Training Planner")
    st.caption("Sign in or create an account to access your private training data.")
    notice = st.session_state.pop("auth_notice", None)
    if notice:
        st.warning(notice)
    sign_in_tab, sign_up_tab = st.tabs(["Sign in", "Create account"])
    with sign_in_tab:
        with st.form("account_sign_in"):
            email = st.text_input("Email", key="sign_in_email")
            password = st.text_input("Password", type="password", key="sign_in_password")
            submitted = st.form_submit_button("Sign in", type="primary")
        if submitted:
            try:
                sign_in(email.strip(), password, api_key)
                st.rerun()
            except AuthError as exc:
                st.error(str(exc))
    with sign_up_tab:
        with st.form("account_sign_up"):
            email = st.text_input("Email", key="sign_up_email")
            password = st.text_input("Password", type="password", key="sign_up_password")
            confirm = st.text_input("Confirm password", type="password", key="sign_up_confirm")
            submitted = st.form_submit_button("Create account")
        if submitted:
            if not email.strip() or not password:
                st.error("Enter an email address and password.")
            elif password != confirm:
                st.error("The passwords do not match.")
            else:
                try:
                    signed_in = sign_up(email.strip(), password, api_key)
                    if signed_in:
                        st.rerun()
                    else:
                        st.success("Account created. Check your email to confirm it, then sign in.")
                except AuthError as exc:
                    st.error(str(exc))
    return False
