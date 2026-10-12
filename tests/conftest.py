import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.session import configure, session_scope  # noqa: E402
from models.schemas import PlanSettings  # noqa: E402
from services import plan_service as ps  # noqa: E402

START = date(2026, 10, 12)


@pytest.fixture
def db():
    configure("sqlite://")
    with session_scope() as s:
        user = ps.ensure_user(s)
        cfg = PlanSettings(plan_start=START, peak_long_km=32.0, start_long_km=14.0, taper_weeks=3)
        ps.save_settings(s, user, cfg)
        ps.sync_plan(s, user, cfg, today=START)
    yield