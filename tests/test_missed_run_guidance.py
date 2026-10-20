from pathlib import Path

from streamlit.testing.v1 import AppTest

from services import auth


GUIDANCE_PHRASES = (
    "keeps it on its scheduled day",
    "no catch-up run is moved or added",
    "records 0 km actual",
    "lower running completion",
    "affect later running volume",
    "next long run is capped at the missed run's planned distance",
    "other adjustments may make it shorter",
)


def _run_page(monkeypatch, page_name):
    from utils import bootstrap

    monkeypatch.setattr(bootstrap, "ready", lambda: None)
    monkeypatch.setattr(auth, "runtime_config", lambda: (None, None, None))
    page = Path(__file__).resolve().parent.parent / "pages" / page_name
    return AppTest.from_file(page).run()


def _rendered_copy(app):
    return "\n".join(
        element.value
        for collection in (app.caption, app.info, app.markdown)
        for element in collection
    )


def _assert_guidance_present(app):
    copy = _rendered_copy(app)
    for phrase in GUIDANCE_PHRASES:
        assert phrase in copy


def test_weekly_planner_explains_missed_run_effects_near_status_control(db, monkeypatch):
    app = _run_page(monkeypatch, "training_plan.py")

    assert not app.exception
    _assert_guidance_present(app)
    assert any(selectbox.label == "Status" for selectbox in app.selectbox)


def test_dashboard_reminds_and_routes_to_weekly_planner(db, monkeypatch):
    import streamlit as st

    destinations = []
    monkeypatch.setattr(st, "switch_page", destinations.append)
    app = _run_page(monkeypatch, "dashboard.py")

    assert not app.exception
    _assert_guidance_present(app)
    action = next(button for button in app.button if button.label == "Open Weekly Planner")
    action.click().run()

    assert not app.exception
    assert destinations == ["pages/training_plan.py"]
