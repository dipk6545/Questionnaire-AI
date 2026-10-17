"""Persisted identities, assessments, responses, and performance records."""
from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, String, Text, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from backend.app.models.user import Base


def now():
    return datetime.now(timezone.utc)


class LoginEvent(Base):
    __tablename__ = 'login_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LoginSession(Base):
    __tablename__ = 'login_sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OAuthState(Base):
    __tablename__ = 'oauth_states'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AssessmentAttempt(Base):
    __tablename__ = 'assessment_attempts'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    subject: Mapped[str] = mapped_column(String(40))
    level: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default='generating')
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(30), default='professional-v1')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Question(Base):
    __tablename__ = 'questions'
    __table_args__ = (UniqueConstraint('attempt_id', 'position'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey('assessment_attempts.id'), index=True)
    position: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
    topic: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(10))
    options: Mapped[list] = mapped_column(JSON)
    correct_options: Mapped[list] = mapped_column(JSON)
    explanation: Mapped[str] = mapped_column(Text)


class Answer(Base):
    __tablename__ = 'answers'
    __table_args__ = (UniqueConstraint('attempt_id', 'question_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey('assessment_attempts.id'), index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey('questions.id'))
    selected_options: Mapped[list] = mapped_column(JSON)
    correct: Mapped[bool | None] = mapped_column(nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Result(Base):
    __tablename__ = 'results'
    attempt_id: Mapped[str] = mapped_column(ForeignKey('assessment_attempts.id'), primary_key=True)
    score: Mapped[int]
    total: Mapped[int]
    percentage: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Feedback(Base):
    __tablename__ = 'feedback'
    attempt_id: Mapped[str] = mapped_column(ForeignKey('assessment_attempts.id'), primary_key=True)
    summary: Mapped[str] = mapped_column(Text)
    strengths: Mapped[list] = mapped_column(JSON)
    focus_areas: Mapped[list] = mapped_column(JSON)
    next_steps: Mapped[list] = mapped_column(JSON)
    model: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
