"""Question bank table storing pre-generated, deduplicated MCQs."""
from datetime import datetime, timezone
from sqlalchemy import DateTime, String, Text, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.models.user import Base


def now():
    return datetime.now(timezone.utc)


class QuestionBank(Base):
    __tablename__ = 'question_bank'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    hash_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # SHA-256 of normalized question text
    subject: Mapped[str] = mapped_column(String(40), index=True)
    level: Mapped[str] = mapped_column(String(30), index=True)
    topic: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(10))  # 'single' or 'multiple'
    text: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSON)
    correct_options: Mapped[list] = mapped_column(JSON)
    explanation: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    __table_args__ = (
        Index('ix_question_bank_subject_level', 'subject', 'level'),
    )
