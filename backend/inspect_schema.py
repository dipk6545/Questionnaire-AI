"""Print table definitions only; never prints credentials or student data."""
import asyncio
from sqlalchemy import inspect
from backend.app.db.session import async_engine


def describe(connection):
    inspector = inspect(connection)
    for name in ['users', 'login_events', 'login_sessions', 'oauth_states', 'assessment_attempts',
                 'questions', 'answers', 'results', 'feedback', 'alembic_version']:
        if inspector.has_table(name):
            print(name, [(c['name'], str(c['type']), c['nullable']) for c in inspector.get_columns(name)])


async def main():
    async with async_engine.connect() as connection:
        await connection.run_sync(describe)
    await async_engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
