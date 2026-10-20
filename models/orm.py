import datetime as dt
from typing import Optional

from sqlalchemy import (
    Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (Index("uq_users_auth_uid", "auth_uid", unique=True),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    auth_uid: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.now)


class AthleteProfile(Base):
    __tablename__ = "athlete_profiles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    training_focus: Mapped[str] = mapped_column(String(20), default="Both")
    experience_level: Mapped[str] = mapped_column(String(20), default="Beginner")
    age: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    body_weight_kg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    goal: Mapped[str] = mapped_column(Text, default="Build consistent fitness")
    share_garmin_recovery_with_ai: Mapped[bool] = mapped_column(Boolean, default=False)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.now, onupdate=dt.datetime.now)


class Friendship(Base):
    __tablename__ = "friendships"
    __table_args__ = (UniqueConstraint("user_id", "friend_id", name="uq_friendship_pair"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    friend_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.now)


class Setting(Base):
    __tablename__ = "settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    key: Mapped[str] = mapped_column(String(50))
    value: Mapped[str] = mapped_column(Text)


class TrainingWeek(Base):
    __tablename__ = "training_weeks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    week_no: Mapped[int] = mapped_column(Integer, default=0)
    week_start: Mapped[dt.date] = mapped_column(Date)
    phase: Mapped[str] = mapped_column(String(50), default="")
    is_recovery: Mapped[bool] = mapped_column(Boolean, default=False)
    planned_km: Mapped[float] = mapped_column(Float, default=0.0)
    adjusted_km: Mapped[float] = mapped_column(Float, default=0.0)
    actual_km: Mapped[float] = mapped_column(Float, default=0.0)
    completion_pct: Mapped[float] = mapped_column(Float, default=0.0)


class RunningSession(Base):
    __tablename__ = "running_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    week_id: Mapped[Optional[int]] = mapped_column(ForeignKey("training_weeks.id"), nullable=True)
    date: Mapped[dt.date] = mapped_column(Date)
    weekday: Mapped[int] = mapped_column(Integer, default=0)
    slot: Mapped[str] = mapped_column(String(20), default="")  # quality | long | weekend | race
    name: Mapped[str] = mapped_column(String(100), default="")
    run_type: Mapped[str] = mapped_column(String(40), default="Easy")
    variant: Mapped[str] = mapped_column(String(40), default="")
    adjusted_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    planned_km: Mapped[float] = mapped_column(Float, default=0.0)
    adjusted_km: Mapped[float] = mapped_column(Float, default=0.0)
    actual_km: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String, default="planned")  # planned | completed | missed
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    garmin_id: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str | None] = mapped_column(String, nullable=True)  # "manual" | "garmin"
    manual_lock: Mapped[bool] = mapped_column(Boolean, default=False)


class StrengthSession(Base):
    __tablename__ = "strength_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    week_id: Mapped[Optional[int]] = mapped_column(ForeignKey("training_weeks.id"), nullable=True)
    date: Mapped[dt.date] = mapped_column(Date)
    weekday: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(20))  # Push | Pull | Legs
    core_minutes: Mapped[int] = mapped_column(Integer, default=0)
    volume_level: Mapped[str] = mapped_column(String(20), default="normal")
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self):
        return f"<StrengthSession(id={self.id}, user_id={self.user_id}, date={self.date}, kind={self.kind}, completed={self.completed})>"

    def update_status(self):
        self.status = "completed" if self.completed else "planned"


class StrengthLog(Base):
    __tablename__ = "strength_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    date: Mapped[dt.date] = mapped_column(Date)
    exercise: Mapped[str] = mapped_column(String)
    weight_kg: Mapped[float] = mapped_column(Float)
    sets: Mapped[int] = mapped_column(Integer)
    reps: Mapped[int] = mapped_column(Integer)  # reps per set (lowest set)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(120))
    event_type: Mapped[str] = mapped_column(String(40))
    date: Mapped[dt.date] = mapped_column(Date)
    distance_km: Mapped[float] = mapped_column(Float, default=0.0)
    priority: Mapped[str] = mapped_column(String(1), default="C")
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Note(Base):
    __tablename__ = "notes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    date: Mapped[dt.date] = mapped_column(Date)
    category: Mapped[str] = mapped_column(String(30), default="Training")
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.now)


class AIAdjustment(Base):
    __tablename__ = "ai_adjustments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.now)
    session_date: Mapped[dt.date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(30))
    old_km: Mapped[float] = mapped_column(Float)
    new_km: Mapped[float] = mapped_column(Float)
    old_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    new_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected