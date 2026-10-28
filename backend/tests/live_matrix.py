"""Opt-in 32-case live OpenAI + PostgreSQL end-to-end assessment audit.

Uses the real API handlers and providers in an isolated disposable PostgreSQL
schema. Public student records are read only for aggregate storage diagnostics.
Run: python -u -m backend.tests.live_matrix --concurrency 3
Reports contain status/counts and rejection reasons, never API keys or answers.
"""
import argparse
import asyncio
import contextvars
import csv
import json
import logging
import time
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.main import app
from app.core.config import get_settings
from app.db.session import get_db_session
from app.models.user import Base, User
from app.models.examination import AssessmentAttempt, LoginSession, Question, Answer, Result, Feedback, now
from app.api.v1.endpoints.auth import digest
from app.services import examination as service

active_case = contextvars.ContextVar('matrix_case', default=None)


class Rejections(logging.Handler):
    def emit(self, record):
        row = active_case.get()
        if row is not None:
            row['events'].append(record.getMessage()[:2500])


def write_report(report, folder):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'matrix.json'
    temporary = folder / 'matrix.tmp'
    temporary.write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    temporary.replace(path)
    columns = ['subject', 'level', 'status', 'stage', 'http_status', 'questions', 'answers',
               'results', 'feedback', 'attempt_status', 'seconds', 'error']
    with (folder / 'matrix.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction='ignore')
        writer.writeheader(); writer.writerows(report['cases'])
    lines = ['# Live assessment audit', '', f"Model: `{report['model']}`. Run: {report['started_at']}.",
             '', '| Subject | Level | Outcome | Stage | Questions | Answers | Results | Feedback |',
             '|---|---|---|---|---:|---:|---:|---:|']
    for r in report['cases']:
        lines.append('| ' + ' | '.join(str(r.get(k, '')) for k in
            ['subject', 'level', 'status', 'stage', 'questions', 'answers', 'results', 'feedback']) + ' |')
    lines += ['', '## Database', '', json.dumps(report.get('database', {}), indent=2, default=str),
              '', '## Failures', '']
    for r in report['cases']:
        if r['status'] == 'FAIL':
            lines += [f"### {r['subject']} / {r['level']}", '', r.get('error', ''), '', *r['events'], '']
    lines += ['', '## Successful cases that required repair', '']
    for r in report['cases']:
        if r['status'] == 'PASS' and r['events']:
            lines += [f"### {r['subject']} / {r['level']}", '', *r['events'], '']
    (folder / 'matrix.md').write_text('\n'.join(lines), encoding='utf-8')


async def main(args):
    settings = get_settings()
    if not settings.DATABASE_URL.startswith('postgresql') or not settings.OPENAI_API_KEY:
        raise RuntimeError('Configured PostgreSQL and OpenAI API key are required.')
    subjects = list(service.SUBJECT_TOPICS)
    cases = [(s, l) for s in subjects for l in service.LEVELS]
    if args.failed_from:
        previous = json.loads(Path(args.failed_from).read_text(encoding='utf-8'))
        cases = [(r['subject'], r['level']) for r in previous['cases'] if r['status'] != 'PASS']
    report = {'started_at': now().isoformat(), 'model': settings.OPENAI_MODEL,
              'prompt_version': service.PROMPT_VERSION, 'database': {}, 'cases': []}
    folder = Path(args.output)
    schema = 'questionnaire_matrix_' + uuid4().hex
    admin = create_async_engine(settings.DATABASE_URL, connect_args={'statement_cache_size': 0},
                                pool_pre_ping=True, pool_recycle=300)
    engine = create_async_engine(settings.DATABASE_URL, connect_args={'statement_cache_size': 0},
                                 pool_pre_ping=True, pool_recycle=300,
                                 execution_options={'schema_translate_map': {None: schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    created = False
    report['database']['test_schema'] = schema
    handler = Rejections()
    service.logger.addHandler(handler)
    try:
        started = time.monotonic()
        async with admin.begin() as connection:
            assert await connection.scalar(text('SELECT 1')) == 1
            report['database']['connection'] = 'PASS'
            report['database']['connection_seconds'] = round(time.monotonic()-started, 2)
            report['database']['migration'] = await connection.scalar(text('SELECT version_num FROM alembic_version'))
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        async with async_sessionmaker(admin)() as db:
            recent = (await db.scalars(select(AssessmentAttempt).order_by(AssessmentAttempt.created_at.desc()).limit(10))).all()
            report['database']['public_recent_attempts'] = []
            for a in recent:
                entry = {'subject': a.subject, 'level': a.level, 'status': a.status, 'created_at': a.created_at.isoformat()}
                for model in [Question, Answer, Result, Feedback]:
                    entry[model.__tablename__] = await db.scalar(select(func.count()).select_from(model).where(model.attempt_id == a.id))
                report['database']['public_recent_attempts'].append(entry)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async def isolated_db():
            async with sessions() as db:
                yield db

        app.dependency_overrides[get_db_session] = isolated_db
        async with sessions() as db:
            for i, (subject, level) in enumerate(cases, 1):
                db.add(User(id=i, email=f'matrix-{i}@example.test', name='Matrix test', google_sub=f'matrix-{i}'))
            await db.flush()
            for i in range(1, len(cases)+1):
                db.add(LoginSession(user_id=i, token_hash=digest(f'matrix-{i}'), expires_at=now()+timedelta(hours=4)))
            await db.commit()
        semaphore = asyncio.Semaphore(args.concurrency)

        async def run_case(index, subject, level):
            async with semaphore:
                row = {'subject': subject, 'level': level, 'status': 'RUNNING', 'stage': 'generation',
                       'questions': 0, 'answers': 0, 'results': 0, 'feedback': 0, 'events': []}
                report['cases'].append(row)
                context = active_case.set(row)
                started = time.monotonic()
                attempt_id = str(uuid4())
                print(f'START {subject} / {level}', flush=True)
                write_report(report, folder)
                try:
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                        base_url='http://matrix', headers={'Authorization': f'Bearer matrix-{index}'}, timeout=360) as client:
                        response = await client.post('/api/v1/examinations', json={
                            'request_id': attempt_id, 'subject': subject, 'level': level})
                        row['http_status'] = response.status_code
                        if response.status_code != 200:
                            raise RuntimeError(response.json().get('detail', 'Generation request failed') if response.headers.get('content-type', '').startswith('application/json') else 'Backend returned a non-JSON error')
                        exam = response.json()
                        assert len(exam['questions']) == 5
                        assert sum(q['kind'] == 'multiple' for q in exam['questions']) == 1
                        assert all('correct_options' not in q and 'explanation' not in q for q in exam['questions'])
                        row['stage'] = 'question_persistence'
                        async with sessions() as db:
                            questions = (await db.scalars(select(Question).where(Question.attempt_id == attempt_id))).all()
                            assert len(questions) == 5
                            assert {q.id for q in questions} == {q['id'] for q in exam['questions']}
                            keys = {q.id: q.correct_options for q in questions}
                        batch = {'answers': [{'question_id': q['id'], 'selected_options': [0, 1] if q['kind'] == 'multiple' else [0]}
                                             for q in exam['questions']]}
                        expected = sum(set(a['selected_options']) == set(keys[a['question_id']]) for a in batch['answers'])
                        row['stage'] = 'draft_save'
                        saved = await client.put(f'/api/v1/examinations/{attempt_id}/answers', json=batch)
                        assert saved.status_code == 200, f'Draft status {saved.status_code}'
                        resumed = await client.get(f'/api/v1/examinations/{attempt_id}')
                        assert resumed.status_code == 200
                        assert all(q['selected_options'] for q in resumed.json()['questions'])
                        row['stage'] = 'submission_feedback'
                        submitted = await client.post(f'/api/v1/examinations/{attempt_id}/submit', json=batch)
                        assert submitted.status_code == 200, f'Submit status {submitted.status_code}'
                        result = submitted.json()
                        assert result['score'] == expected and result['percentage'] == expected*20
                        if result['feedback_status'] != 'ready':
                            result = (await client.post(f'/api/v1/examinations/{attempt_id}/feedback')).json()
                        assert result['feedback_status'] == 'ready', 'Score saved but feedback remains pending'
                        row['stage'] = 'result_persistence'
                        async with sessions() as db:
                            assert (await db.get(Result, attempt_id)).score == expected
                            assert (await db.get(Feedback, attempt_id)).summary
                        repeat = await client.post(f'/api/v1/examinations/{attempt_id}/submit', json=batch)
                        assert repeat.status_code == 200 and repeat.json()['score'] == expected
                        history = (await client.get('/api/v1/examinations/history')).json()
                        assert len(history['attempts']) == 1 and len(history['performance']) == 1
                        row['stage'], row['status'] = 'complete', 'PASS'
                except Exception as error:
                    row['status'] = 'FAIL'
                    row['error'] = str(error)[:1000] if isinstance(error, (RuntimeError, AssertionError)) else type(error).__name__
                finally:
                    try:
                        async with sessions() as db:
                            a = await db.get(AssessmentAttempt, attempt_id)
                            row['attempt_status'] = a.status if a else 'not_created'
                            for model, field in [(Question, 'questions'), (Answer, 'answers'), (Result, 'results'), (Feedback, 'feedback')]:
                                row[field] = await db.scalar(select(func.count()).select_from(model).where(model.attempt_id == attempt_id))
                    except Exception as error:
                        row['database_error'] = type(error).__name__
                        row['status'] = 'FAIL'
                    row['seconds'] = round(time.monotonic()-started, 1)
                    active_case.reset(context)
                    write_report(report, folder)
                    print(f"{row['status']} {subject} / {level} | stage={row['stage']} | Q/A/R/F={row['questions']}/{row['answers']}/{row['results']}/{row['feedback']} | {row.get('error', '')}", flush=True)

        await asyncio.gather(*(run_case(i, s, l) for i, (s, l) in enumerate(cases, 1)))
        report['passed'] = sum(r['status'] == 'PASS' for r in report['cases'])
        report['failed'] = sum(r['status'] == 'FAIL' for r in report['cases'])
        report['finished_at'] = now().isoformat()
        print(f"TOTAL {report['passed']}/{len(cases)} passed; {report['failed']} failed", flush=True)
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        service.logger.removeHandler(handler)
        await engine.dispose()
        try:
            if created:
                assert schema.startswith('questionnaire_matrix_') and schema.isidentifier() and len(schema) == 53
                async with admin.begin() as connection:
                    await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
                report['database']['test_schema_removed'] = True
        except Exception as error:
            report['database']['cleanup_error'] = type(error).__name__
            raise
        finally:
            await admin.dispose()
            write_report(report, folder)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--concurrency', type=int, choices=range(1, 5), default=3)
    parser.add_argument('--output', default='artifacts/live-matrix')
    parser.add_argument('--failed-from')
    asyncio.run(main(parser.parse_args()))
