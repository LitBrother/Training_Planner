from sqlalchemy import select

from models.orm import AthleteProfile
from models.schemas import AthleteProfileInput
from services import plan_service as ps

LEVEL_DEFAULTS = {
    "Beginner": (5.0, 12.0),
    "Intermediate": (8.0, 20.0),
    "Advanced": (12.0, 28.0),
}


def get_profile(s, user) -> AthleteProfile:
    profile = s.scalars(select(AthleteProfile).where(AthleteProfile.user_id == user.id)).first()
    if profile is None:
        profile = AthleteProfile(user_id=user.id)
        s.add(profile)
        s.flush()
    return profile


def save_profile(s, user, values: dict) -> AthleteProfile:
    data = AthleteProfileInput.model_validate(values)
    profile = get_profile(s, user)
    first_setup = not profile.completed
    profile.training_focus = data.training_focus
    profile.experience_level = data.experience_level
    profile.age = data.age
    profile.body_weight_kg = data.body_weight_kg
    profile.goal = data.goal.strip()
    profile.share_garmin_recovery_with_ai = data.share_garmin_recovery_with_ai
    profile.completed = True
    user.name = data.user_name.strip()

    cfg = ps.get_settings(s, user)
    updates = {
        "user_name": data.user_name.strip(),
        "training_focus": data.training_focus,
        "goal": data.goal.strip(),
    }
    if first_setup:
        updates["start_long_km"], updates["peak_long_km"] = LEVEL_DEFAULTS[data.experience_level]
    ps.save_settings(s, user, cfg.model_copy(update=updates))
    ps.sync_plan(s, user, ps.get_settings(s, user))
    s.flush()
    return profile


def as_context(profile: AthleteProfile) -> dict:
    return {
        "training_focus": profile.training_focus,
        "experience_level": profile.experience_level,
        "age_years": profile.age,
        "body_weight_kg": profile.body_weight_kg,
        "goal": profile.goal,
        "share_garmin_recovery_with_ai": profile.share_garmin_recovery_with_ai,
    }
