"""Offline integration tests: real SQL persistence, mocked external providers."""
import os
import time
import tempfile
import unittest
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///:memory:'

import httpx
from alembic import command
from alembic.config import Config
from sqlalchemy import select, func
from app.main import app
from app.db.session import async_engine, SessionLocal, get_db_session
from app.models.user import Base, User
from app.models.examination import LoginSession, LoginEvent, OAuthState, AssessmentAttempt, Answer, Result, Feedback, now
from app.api.v1.endpoints import auth
from app.schemas.examination import QuestionSet, GenerationPlan, ReviewPlan, PerformanceFeedback
from app.services import examination as service


def question_set():
    return QuestionSet.model_validate({'questions': [
        {'text': f'A SQL query scenario {i}: which index supports the supplied range predicate?',
         'topic': 'indexes', 'kind': 'multiple' if i == 4 else 'single',
         'options': ['B-tree', 'Hash index', 'No index', 'Full text index'],
         'correct_options': [0, 1] if i == 4 else [0],
         'explanation': 'The index must match the stated predicate and access pattern.'}
        for i in range(5)]})


FEEDBACK = PerformanceFeedback(summary='Review query access patterns before choosing indexes.',
    strengths=['Basic index selection'], focus_areas=['Multiple-index tradeoffs'], next_steps=['Compare two EXPLAIN plans.'])


class AssessmentFlow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        async with SessionLocal() as db:
            db.add_all([User(id=1, email='a@example.test', name='A', google_sub='a'),
                        User(id=2, email='b@example.test', name='B', google_sub='b')])
            await db.flush()
            db.add_all([LoginSession(token_hash=auth.digest('a'), user_id=1, expires_at=now()+timedelta(hours=1)),
                        LoginSession(token_hash=auth.digest('b'), user_id=2, expires_at=now()+timedelta(hours=1))])
            await db.commit()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                       headers={'Authorization': 'Bearer a'})
        self.generation = patch.object(service, 'generate_questions', AsyncMock(return_value=question_set()))
        self.feedback = patch.object(service, 'generate_feedback', AsyncMock(return_value=FEEDBACK))
        self.generate_mock, self.feedback_mock = self.generation.start(), self.feedback.start()

    async def asyncTearDown(self):
        self.generation.stop(); self.feedback.stop()
        await self.client.aclose()
        await async_engine.dispose()

    async def create(self, **kwargs):
        request = {'request_id': str(uuid.uuid4()), 'subject': 'SQL', 'level': 'Production grade', **kwargs}
        response = await self.client.post('/api/v1/examinations', json=request)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json(), request

    def batch(self, exam, last=None):
        return {'answers': [{'question_id': q['id'], 'selected_options':
                            (last if last is not None else [0, 1]) if q['kind'] == 'multiple' else [0]}
                           for q in exam['questions']]}

    async def test_persist_resume_submit_history_and_idempotency(self):
        exam, request = await self.create()
        self.assertEqual(len(exam['questions']), 5)
        self.assertEqual(sum(q['kind'] == 'multiple' for q in exam['questions']), 1)
        self.assertNotIn('correct_options', exam['questions'][0])
        self.assertNotIn('explanation', exam['questions'][0])
        repeated = await self.client.post('/api/v1/examinations', json=request)
        self.assertEqual(repeated.json()['id'], exam['id'])
        self.assertEqual(self.generate_mock.await_count, 1)
        path = '/api/v1/examinations/' + exam['id']
        batch = self.batch(exam, last=[0])  # Partial multi-selection gets zero credit.
        self.assertEqual((await self.client.put(path+'/answers', json=batch)).status_code, 200)
        resumed = (await self.client.get(path)).json()
        self.assertEqual(resumed['questions'][0]['selected_options'], [0])
        self.assertEqual((await self.client.get(path+'/result')).status_code, 409)
        response = await self.client.post(path+'/submit', json=batch)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['score'], 4)
        self.assertEqual(response.json()['percentage'], 80)
        self.assertEqual(response.json()['feedback']['summary'], FEEDBACK.summary)
        self.assertEqual((await self.client.post(path+'/submit', json=batch)).status_code, 200)
        self.assertEqual(self.feedback_mock.await_count, 1)
        self.assertEqual((await self.client.post(path+'/submit', json=self.batch(exam))).status_code, 409)
        self.assertEqual((await self.client.put(path+'/answers', json=batch)).status_code, 409)
        history = (await self.client.get('/api/v1/examinations/history')).json()
        self.assertEqual(history['performance'][0]['average'], 80)
        async with SessionLocal() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(Answer)), 5)
            self.assertEqual(await db.scalar(select(func.count()).select_from(Result)), 1)
            self.assertEqual(await db.scalar(select(func.count()).select_from(Feedback)), 1)

    async def test_owner_isolation_and_expired_sessions(self):
        exam, _ = await self.create()
        path = '/api/v1/examinations/' + exam['id']
        other = {'Authorization': 'Bearer b'}
        self.assertEqual((await self.client.get(path, headers=other)).status_code, 404)
        self.assertEqual((await self.client.post(path+'/submit', json=self.batch(exam), headers=other)).status_code, 404)
        self.assertEqual((await self.client.get('/api/v1/examinations/history', headers=other)).json()['attempts'], [])
        self.assertEqual((await self.client.get(path, headers={'Authorization': 'Bearer bogus'})).status_code, 401)
        async with SessionLocal() as db:
            session = await db.get(LoginSession, auth.digest('a'))
            session.expires_at = now() - timedelta(seconds=1)
            await db.commit()
        self.assertEqual((await self.client.get(path)).status_code, 401)

    async def test_payload_validation(self):
        invalid = await self.client.post('/api/v1/examinations', json={
            'request_id': str(uuid.uuid4()), 'subject': 'Unrelated subject', 'level': 'Easy'})
        self.assertEqual(invalid.status_code, 422)
        exam, _ = await self.create()
        path = '/api/v1/examinations/' + exam['id']
        batch = self.batch(exam)
        self.assertEqual((await self.client.post(path+'/submit', json={'answers': batch['answers'][:1]})).status_code, 422)
        batch['answers'][0]['selected_options'] = [True]
        self.assertEqual((await self.client.put(path+'/answers', json=batch)).status_code, 422)
        batch['answers'][0]['selected_options'] = [0, 1]
        self.assertEqual((await self.client.put(path+'/answers', json=batch)).status_code, 422)
        batch = self.batch(exam, last=[0, 1, 2, 3])
        response = await self.client.post(path+'/submit', json=batch)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['score'], 4)

    async def test_feedback_failure_preserves_score_and_retry(self):
        from fastapi import HTTPException
        exam, _ = await self.create()
        path = '/api/v1/examinations/' + exam['id']
        self.feedback_mock.side_effect = HTTPException(502, 'Provider unavailable')
        response = await self.client.post(path+'/submit', json=self.batch(exam))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['score'], 5)
        self.assertEqual(response.json()['feedback_status'], 'pending')
        self.feedback_mock.side_effect = None
        response = await self.client.post(path+'/feedback')
        self.assertEqual(response.json()['feedback_status'], 'ready')
        self.assertEqual(response.json()['score'], 5)

    async def test_generation_failure_is_recorded_and_discard_frees_limit(self):
        from fastapi import HTTPException
        self.generate_mock.side_effect = HTTPException(502, 'Review rejected')
        response = await self.client.post('/api/v1/examinations', json={
            'request_id': str(uuid.uuid4()), 'subject': 'Python', 'level': 'Hard'})
        self.assertEqual(response.status_code, 502)
        history = (await self.client.get('/api/v1/examinations/history')).json()
        self.assertEqual(history['attempts'][0]['status'], 'failed')
        self.generate_mock.side_effect = None
        exams = [(await self.create())[0] for _ in range(3)]
        response = await self.client.post('/api/v1/examinations', json={
            'request_id': str(uuid.uuid4()), 'subject': 'SQL', 'level': 'Easy'})
        self.assertEqual(response.status_code, 429)
        self.assertEqual((await self.client.post('/api/v1/examinations/'+exams[0]['id']+'/discard')).status_code, 200)
        await self.create()

    async def test_oauth_identity_verification_login_tracking_and_logout(self):
        self.assertEqual((await self.client.get('/api/v1/auth/authenticate?code=x&state=missing')).status_code, 400)
        async with SessionLocal() as db:
            db.add(OAuthState(token_hash=auth.digest('state'), expires_at=now()+timedelta(minutes=10)))
            await db.commit()
        provider = AsyncMock()
        provider.post.return_value = httpx.Response(200, json={'id_token': 'signed-token'})
        provider.get.return_value = httpx.Response(200, json={
            'aud': 'real-client', 'iss': 'https://accounts.google.com', 'exp': time.time()+1000,
            'email_verified': 'true', 'sub': 'a', 'email': 'a@example.test'})
        with patch.dict(os.environ, {'GOOGLE_CLIENT_ID': 'real-client'}), patch.object(auth.httpx, 'AsyncClient') as factory:
            factory.return_value.__aenter__.return_value = provider
            response = await self.client.get('/api/v1/auth/authenticate?code=code&state=state')
        self.assertEqual(response.status_code, 200, response.text)
        token = response.json()['token']
        async with SessionLocal() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(LoginEvent)), 1)
            self.assertIsNotNone(await db.get(LoginSession, auth.digest(token)))
            self.assertIsNone(await db.get(LoginSession, token))
        self.assertEqual((await self.client.get('/api/v1/auth/authenticate?code=code&state=state')).status_code, 400)
        await self.client.post('/api/v1/auth/logout', headers={'Authorization': 'Bearer '+token})
        self.assertEqual((await self.client.get('/api/v1/auth/me', headers={'Authorization': 'Bearer '+token})).status_code, 401)


class QualityRules(unittest.IsolatedAsyncioTestCase):
    def plan(self):
        questions = question_set().questions
        return GenerationPlan(single_questions=[q.model_dump() for q in questions[:4]],
                              multiple_question=questions[4].model_dump())

    def review(self, suitable=True):
        return ReviewPlan(suitable=suitable, reason='Reviewed' if suitable else 'Off subject',
                          single_questions=[{'correct_options': [0], 'explanation': 'A validated answer explanation.'} for _ in range(4)],
                          multiple_question={'correct_options': [0, 1], 'explanation': 'Two options are correct in this scenario.'})

    async def test_provider_schema_enforces_required_mix_and_answer_counts(self):
        from pydantic import ValidationError
        payload = self.plan().model_dump()
        payload['single_questions'][0]['correct_options'] = [0, 1]
        with self.assertRaises(ValidationError):
            GenerationPlan.model_validate(payload)
        payload = self.plan().model_dump()
        payload['multiple_question']['correct_options'] = [0]
        with self.assertRaises(ValidationError):
            GenerationPlan.model_validate(payload)
        payload = self.plan().model_dump()
        payload['single_questions'].pop()
        with self.assertRaises(ValidationError):
            GenerationPlan.model_validate(payload)
        self.assertEqual(len(self.plan().as_question_set().questions), 5)

    async def test_reviewer_cannot_change_required_mix(self):
        questions = self.plan()
        review = self.review()
        review.multiple_question.correct_options = [0, 0]
        with patch.object(service, 'completion', AsyncMock(side_effect=[questions, review, questions, review])):
            from fastapi import HTTPException
            with self.assertRaises(HTTPException):
                await service.generate_questions('SQL', 'Hard')

    async def test_context_and_production_instructions_and_rejection(self):
        review = self.review(suitable=False)
        with patch.object(service, 'completion', AsyncMock(side_effect=[self.plan(), review, self.plan(), review])) as llm:
            from fastapi import HTTPException
            with self.assertRaises(HTTPException):
                await service.generate_questions('SQL', 'Production grade')
            self.assertEqual(llm.call_args_list[0].args[2]['allowed_topics'], service.SUBJECT_TOPICS['SQL'])
            self.assertIn('debugging, deployment, reliability', llm.call_args_list[0].args[1])

    async def test_invalid_generation_retries_with_validation_feedback(self):
        questions = self.plan()
        review = self.review()
        with patch.object(service, 'completion', AsyncMock(side_effect=[
            service.InvalidAssessment('Exactly one multiple-answer question required'), questions, review])) as llm:
            generated = await service.generate_questions('Python', 'Hard')
            self.assertEqual(len(generated.questions), 5)
            self.assertIn('previous_validation_issues_to_fix', llm.call_args_list[1].args[2])
            self.assertEqual(llm.await_count, 3)

    async def test_configuration_failure_is_not_retried(self):
        from fastapi import HTTPException
        with patch.object(service, 'completion', AsyncMock(side_effect=HTTPException(503, 'Invalid key'))) as llm:
            with self.assertRaises(HTTPException) as caught:
                await service.generate_questions('Python', 'Hard')
            self.assertEqual(caught.exception.status_code, 503)
            self.assertEqual(llm.await_count, 1)

    async def test_rejected_candidate_is_supplied_for_targeted_repair(self):
        rejected, accepted = self.review(suitable=False), self.review()
        with patch.object(service, 'completion', AsyncMock(side_effect=[
            self.plan(), rejected, self.plan(), accepted])) as llm:
            generated = await service.generate_questions('Python', 'Production grade')
            retry = llm.call_args_list[2].args[2]
            self.assertEqual(retry['previous_validation_issues_to_fix']['issues'], 'Off subject')
            self.assertEqual(len(retry['previous_validation_issues_to_fix']['previous_questions']['questions']), 5)
            self.assertIn('CPython 3.11', retry['runtime_assumption'])
            self.assertEqual(len(generated.questions), 5)


class MigrationTests(unittest.TestCase):
    def test_adopt_compatible_existing_tables_without_replacing_records(self):
        from sqlalchemy import create_engine, text
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'existing.db'
            engine = create_engine('sqlite:///'+path.as_posix())
            Base.metadata.create_all(engine)
            with engine.begin() as db:
                db.execute(text("INSERT INTO users (id,email,name,google_sub,created_at) VALUES "
                                "(12,'kept@example.test','Kept','kept',CURRENT_TIMESTAMP)"))
                db.execute(text('INSERT INTO login_events (id,user_id,created_at) VALUES (8,12,CURRENT_TIMESTAMP)'))
            engine.dispose()
            with patch.dict(os.environ, {'DATABASE_URL': 'sqlite+aiosqlite:///'+path.as_posix()}):
                command.upgrade(Config('alembic.ini'), 'head')
            engine = create_engine('sqlite:///'+path.as_posix())
            with engine.connect() as db:
                self.assertEqual(db.scalar(text('SELECT user_id FROM login_events WHERE id=8')), 12)
            engine.dispose()

    def test_upgrade_preserves_existing_users_and_is_repeatable(self):
        import sqlite3
        from contextlib import closing
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'migration.db'
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(255) NOT NULL UNIQUE, '
                           'name VARCHAR(255), google_sub VARCHAR(255) NOT NULL UNIQUE, created_at DATETIME NOT NULL)')
                db.execute("INSERT INTO users VALUES (42, 'existing@example.test', 'Existing', 'existing', CURRENT_TIMESTAMP)")
                db.commit()
            with patch.dict(os.environ, {'DATABASE_URL': 'sqlite+aiosqlite:///'+path.as_posix()}):
                command.upgrade(Config('alembic.ini'), 'head')
                command.upgrade(Config('alembic.ini'), 'head')
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('SELECT id FROM users').fetchone()[0], 42)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM questions').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
