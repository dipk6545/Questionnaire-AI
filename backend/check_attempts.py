"""Read recent assessment status for diagnostics; excludes student identities/answers."""
import asyncio
from sqlalchemy import select, func
from app.db.session import SessionLocal, async_engine
from app.models.examination import AssessmentAttempt, Question


async def main():
    async with SessionLocal() as db:
        rows = (await db.execute(select(AssessmentAttempt.status, AssessmentAttempt.subject,
            AssessmentAttempt.level, AssessmentAttempt.created_at, func.count(Question.id),
            AssessmentAttempt.model, AssessmentAttempt.prompt_version)
            .outerjoin(Question, Question.attempt_id == AssessmentAttempt.id)
            .group_by(AssessmentAttempt.id).order_by(AssessmentAttempt.created_at.desc()).limit(10))).all()
        for status, subject, level, created_at, count, model, prompt in rows:
            print(status, '|', subject, '|', level, '|', created_at.isoformat(), '| questions:', count,
                  '| model:', model, '| prompt:', prompt)
    await async_engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
