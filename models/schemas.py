from datetime import date, timedelta
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from utils.dates import monday_of


class PlanSettings(BaseModel):
    user_name: str = "Athlete"
    race_date: date | None = None      # None = open-ended training, no goal event
    race_distance_km: float = Field(42.2, ge=1, le=100)
    plan_start: date = Field(default_factory=lambda: monday_of(date.today()))
    training_focus: Literal["Running", "Strength", "Both"] = "Both"
    goal: str = Field("Build consistent fitness", min_length=1, max_length=300)
    peak_long_km: float = Field(32.0, ge=8, le=38)
    start_long_km: float = Field(14.0, ge=5, le=30)
    taper_weeks: int = Field(3, ge=1, le=4)
    weekend_day: Literal["Saturday", "Sunday"] = "Saturday"
    openai_model: str = "gemini-3.5-flash-lite"
    horizon_weeks: int = Field(26, ge=4, le=104)

    @property
    def end_date(self) -> date:
        return self.race_date or (self.plan_start + timedelta(weeks=self.horizon_weeks - 1))

    @property
    def effective_taper(self) -> int:
        return self.taper_weeks if self.race_date else 0

    @model_validator(mode="after")
    def _check(self):
        if not self.goal.strip():
            raise ValueError("Enter a training goal.")
        if self.race_date is not None:
            if (monday_of(self.race_date) - monday_of(self.plan_start)).days < 14:
                raise ValueError("Race date must be at least two weeks after the plan start.")
        return self


class AthleteProfileInput(BaseModel):
    user_name: str = Field("Athlete", min_length=1, max_length=100)
    training_focus: Literal["Running", "Strength", "Both"]
    experience_level: Literal["Beginner", "Intermediate", "Advanced"]
    age: int | None = Field(None, ge=18, le=100)
    body_weight_kg: float | None = Field(None, ge=35, le=250)
    goal: str = Field(min_length=1, max_length=300)
    share_garmin_recovery_with_ai: bool = False

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