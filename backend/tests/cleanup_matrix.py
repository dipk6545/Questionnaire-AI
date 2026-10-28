"""Recover interrupted matrix cleanup, verifying only its isolated test schema."""
import argparse
import asyncio
import json
import re
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.config import get_settings
from app.models.examination import now
from backend.tests.live_matrix import write_report


async def main(schema, output):
    if not re.fullmatch(r'questionnaire_matrix_[0-9a-f]{32}', schema):
        raise ValueError('Only generated matrix test schemas can be cleaned up.')
    folder = Path(output)
    report = json.loads((folder / 'matrix.json').read_text(encoding='utf-8'))
    assert len(report['cases']) == 32 and all(r['status'] == 'PASS' for r in report['cases'])
    engine = create_async_engine(get_settings().DATABASE_URL, connect_args={'statement_cache_size': 0},
                                 pool_pre_ping=True, pool_recycle=300)
    try:
        async with engine.begin() as connection:
            exists = await connection.scalar(text('SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name=:name'), {'name': schema})
            if not exists:
                raise RuntimeError('Test schema does not exist; nothing was removed.')
            # This schema must contain exclusively the fixture accounts we generated.
            real_users = await connection.scalar(text(f'SELECT COUNT(*) FROM "{schema}".users WHERE email NOT LIKE \'matrix-%@example.test\''))
            assert real_users == 0, 'Unexpected users in schema; cleanup stopped.'
            totals = {}
            for name in ['users', 'assessment_attempts', 'questions', 'answers', 'results', 'feedback']:
                totals[name] = await connection.scalar(text(f'SELECT COUNT(*) FROM "{schema}"."{name}"'))
            assert totals == {'users': 32, 'assessment_attempts': 32, 'questions': 160,
                              'answers': 160, 'results': 32, 'feedback': 32}, totals
            incomplete = await connection.scalar(text(f'SELECT COUNT(*) FROM "{schema}".assessment_attempts WHERE status != \'submitted\''))
            assert incomplete == 0
            report['database']['verified_totals_before_cleanup'] = totals
            print('Verified PostgreSQL totals:', totals)
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        async with engine.connect() as connection:
            remaining = await connection.scalar(text('SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name=:name'), {'name': schema})
            assert remaining == 0
        report['database']['test_schema'] = schema
        report['database']['test_schema_removed'] = True
        report['database']['cleanup_note'] = 'Initial idle cleanup connection expired; cleanup completed with a fresh connection. Test-case reads/writes all passed.'
        report['passed'], report['failed'] = 32, 0
        report['finished_at'] = now().isoformat()
        write_report(report, folder)
        print('Cleanup verified; public student records unchanged. Final report saved.')
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--schema', required=True)
    parser.add_argument('--output', default='artifacts/live-matrix')
    args = parser.parse_args()
    asyncio.run(main(args.schema, args.output))
