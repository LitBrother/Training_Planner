from datetime import date, timedelta
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

from utils.dates import monday_of


class PlanSettings(BaseModel):
    user_name: str = "Adam Williams"
    race_date: date | None = None      # None = open-ended training, no goal event
    plan_start: date = Field(default_factory=lambda: monday_of(date.today()))
    peak_long_km: float = Field(32.0, ge=20, le=38)
    start_long_km: float = Field(14.0, ge=5, le=30)
    taper_weeks: int = Field(3, ge=1, le=4)
    weekend_day: Literal["Saturday", "Sunday"] = "Saturday"
    openai_model: str = "gemini-3.5-flash-lite"
    horizon_weeks: int = 26            # plan length when there is no race

    @property
    def end_date(self) -> date:
        return self.race_date or (self.plan_start + timedelta(weeks=self.horizon_weeks))

    @property
    def effective_taper(self) -> int:
        return self.taper_weeks if self.race_date else 0

    @model_validator(mode="after")
    def _check(self):
        if self.race_date is not None:
            if (monday_of(self.race_date) - monday_of(self.plan_start)).days < 14:
                raise ValueError("Race date must be at least two weeks after the plan start.")
        return self

    @field_validator("race_date")
    @classmethod
    def _race_ok(cls, v: date | None, info):
        if v is None:
            return None
        start = info.data.get("plan_start")
        if start and monday_of(v) <= monday_of(start):
            raise ValueError("Race date must be after the plan start")
        return v


class AIProposal(BaseModel):
    """Extra fields (e.g. new_date) are forbidden so sessions can never be moved."""
    model_config = ConfigDict(extra="forbid")
    session_date: date
    kind: Literal[
        "reduce_volume", "simplify_quality", "increase_recovery",
        "increase_volume", "restore_volume", "maintain",
    ]
    new_km: float = Field(ge=0)
    new_type: Optional[str] = None
    reason: str = ""