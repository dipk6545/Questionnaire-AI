"""Opt-in PostgreSQL concurrency test in a disposable, isolated schema.

Run from repo root: python -m backend.tests.postgres_smoke
Uses configured PostgreSQL, but never reads or changes public student records.
OpenAI calls are mocked. Requires CREATE SCHEMA permission.
"""
import asyncio
from datetime import timedelta
from uuid import uuid4
from unittest.mock import AsyncMock, patch
import httpx
from sqlalchemy import text, select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core.config import get_settings
from app.main import app
from app.db.session import get_db_session
from app.models.user import Base, User
from app.models.examination import LoginSession, Result, Question, Answer, Feedback, now
from app.api.v1.endpoints.auth import digest
from app.schemas.examination import QuestionSet, PerformanceFeedback
from app.services import examination as service


async def main():
    url = get_settings().DATABASE_URL
    if not url.startswith('postgresql'):
        raise RuntimeError('This test requires a configured PostgreSQL database.')
    schema = 'questionnaire_test_' + uuid4().hex
    admin = create_async_engine(url, connect_args={'statement_cache_size': 0})
    engine = create_async_engine(url, connect_args={'statement_cache_size': 0},
                                 execution_options={'schema_translate_map': {None: schema}})
    created = False
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async def test_db():
            async with sessions() as db:
                yield db

        app.dependency_overrides[get_db_session] = test_db
        async with sessions() as db:
            db.add(User(id=1, email='fixture@example.test', name='Fixture', google_sub='fixture'))
            await db.flush()
            db.add(LoginSession(token_hash=digest('fixture'), user_id=1, expires_at=now()+timedelta(hours=1)))
            await db.commit()
        generated = QuestionSet.model_validate({'questions': [
            {'text': f'A query-plan diagnostic scenario {i}: identify the justified next investigation.',
             'topic': 'query plans', 'kind': 'multiple' if i == 4 else 'single',
             'options': ['Measure the plan', 'Measure write overhead', 'Ignore the workload', 'Disable monitoring'],
             'correct_options': [0, 1] if i == 4 else [0],
             'explanation': 'Measure the query plan and consider the workload before making an index change.'}
            for i in range(5)]})
        feedback = PerformanceFeedback(summary='You selected evidence-based diagnostic steps.',
                                        strengths=['Diagnosis'], focus_areas=[], next_steps=['Compare plans'])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'Authorization': 'Bearer fixture'}, timeout=120) as client:
            with patch.object(service, 'generate_questions', AsyncMock(return_value=generated)) as generation, \
                 patch.object(service, 'generate_feedback', AsyncMock(return_value=feedback)) as mentor:
                request = {'request_id': str(uuid4()), 'subject': 'SQL', 'level': 'Production grade'}
                responses = await asyncio.gather(*[client.post('/api/v1/examinations', json=request) for _ in range(2)])
                # An in-flight duplicate is a 409; a completed duplicate returns the existing exam.
                assert all(r.status_code in (200, 409) for r in responses), [r.status_code for r in responses]
                assert any(r.status_code == 200 for r in responses)
                assert generation.await_count == 1
                exam = next(r.json() for r in responses if r.status_code == 200)
                assert all(r.status_code == 409 or r.json()['id'] == exam['id'] for r in responses)
                batch = {'answers': [{'question_id': q['id'], 'selected_options': [1, 0] if q['kind'] == 'multiple' else [0]}
                                     for q in exam['questions']]}
                results = await asyncio.gather(*[client.post('/api/v1/examinations/'+exam['id']+'/submit', json=batch)
                                                 for _ in range(2)])
                assert all(r.status_code == 200 and r.json()['score'] == 5 for r in results), [r.status_code for r in results]
                assert mentor.await_count == 1
                async with sessions() as db:
                    for model, expected in [(Question, 5), (Answer, 5), (Result, 1), (Feedback, 1)]:
                        assert await db.scalar(select(func.count()).select_from(model)) == expected
        print('PostgreSQL smoke passed: concurrent creation/submission, 5 persisted questions and answers, one result and feedback.')
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        await engine.dispose()
        if created:
            assert schema.startswith('questionnaire_test_') and schema.isidentifier() and len(schema) == 51
            async with admin.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


if __name__ == '__main__':
    asyncio.run(main())
