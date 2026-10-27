"""Add assessment storage, preserving existing users and their IDs."""
from alembic import op
import sqlalchemy as sa

revision = '0001_assessments'
down_revision = None
branch_labels = None
depends_on = None


def create_table(name, *definitions):
    """Adopt compatible tables created by the former create_all startup hook."""
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(name):
        op.create_table(name, *definitions)
        return
    expected = sa.Table(name, sa.MetaData(), *definitions)
    existing = {c['name']: c for c in inspector.get_columns(name)}
    compatible = set(existing) == set(expected.columns.keys())
    for column in expected.columns:
        actual = existing.get(column.name)
        if actual is None:
            compatible = False
            continue
        compatible &= (actual['type']._type_affinity == column.type._type_affinity
                       and actual['nullable'] == column.nullable
                       and getattr(actual['type'], 'length', None) == getattr(column.type, 'length', None)
                       and (op.get_bind().dialect.name == 'sqlite' or
                            getattr(actual['type'], 'timezone', False) == getattr(column.type, 'timezone', False)))
    compatible &= set(inspector.get_pk_constraint(name)['constrained_columns']) == {
        c.name for c in expected.primary_key.columns}
    existing_fks = {(tuple(f['constrained_columns']), f['referred_table'], tuple(f['referred_columns']))
                    for f in inspector.get_foreign_keys(name)}
    for column in expected.columns:
        for fk in column.foreign_keys:
            table, target = fk.target_fullname.split('.')
            compatible &= ((column.name,), table, (target,)) in existing_fks
    existing_unique = {tuple(u['column_names']) for u in inspector.get_unique_constraints(name)}
    for constraint in expected.constraints:
        if isinstance(constraint, sa.UniqueConstraint):
            compatible &= tuple(c.name for c in constraint.columns) in existing_unique
    if not compatible:
        raise RuntimeError(f'Existing {name} schema differs from the migration. No table was replaced; review schema before upgrading.')
    existing_indexes = {tuple(i['column_names']) for i in inspector.get_indexes(name)}
    for index in expected.indexes:
        columns = [c.name for c in index.columns]
        if tuple(columns) not in existing_indexes:
            op.create_index(index.name, name, columns, unique=index.unique)


def upgrade():
    # Existing installations already have users; do not recreate or replace it.
    if not sa.inspect(op.get_bind()).has_table('users'):
        create_table('users', sa.Column('id', sa.Integer, primary_key=True),
            sa.Column('email', sa.String(255), nullable=False, unique=True, index=True),
            sa.Column('name', sa.String(255)),
            sa.Column('google_sub', sa.String(255), nullable=False, unique=True, index=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    create_table('login_events', sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id'), nullable=False, index=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    create_table('login_sessions', sa.Column('token_hash', sa.String(64), primary_key=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id'), nullable=False, index=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False))
    create_table('oauth_states', sa.Column('token_hash', sa.String(64), primary_key=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False))
    create_table('assessment_attempts', sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id'), nullable=False, index=True),
        sa.Column('subject', sa.String(40), nullable=False), sa.Column('level', sa.String(30), nullable=False),
        sa.Column('status', sa.String(20), nullable=False), sa.Column('model', sa.String(100), nullable=False),
        sa.Column('prompt_version', sa.String(30), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('submitted_at', sa.DateTime(timezone=True)))
    create_table('questions', sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('attempt_id', sa.String(36), sa.ForeignKey('assessment_attempts.id'), nullable=False, index=True),
        sa.Column('position', sa.Integer, nullable=False), sa.Column('text', sa.Text, nullable=False),
        sa.Column('topic', sa.String(200), nullable=False), sa.Column('kind', sa.String(10), nullable=False),
        sa.Column('options', sa.JSON, nullable=False), sa.Column('correct_options', sa.JSON, nullable=False),
        sa.Column('explanation', sa.Text, nullable=False), sa.UniqueConstraint('attempt_id', 'position'))
    create_table('answers', sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('attempt_id', sa.String(36), sa.ForeignKey('assessment_attempts.id'), nullable=False, index=True),
        sa.Column('question_id', sa.Integer, sa.ForeignKey('questions.id'), nullable=False),
        sa.Column('selected_options', sa.JSON, nullable=False), sa.Column('correct', sa.Boolean),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('attempt_id', 'question_id'))
    create_table('results',
        sa.Column('attempt_id', sa.String(36), sa.ForeignKey('assessment_attempts.id'), primary_key=True),
        sa.Column('score', sa.Integer, nullable=False), sa.Column('total', sa.Integer, nullable=False),
        sa.Column('percentage', sa.Integer, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    create_table('feedback',
        sa.Column('attempt_id', sa.String(36), sa.ForeignKey('assessment_attempts.id'), primary_key=True),
        sa.Column('summary', sa.Text, nullable=False), sa.Column('strengths', sa.JSON, nullable=False),
        sa.Column('focus_areas', sa.JSON, nullable=False), sa.Column('next_steps', sa.JSON, nullable=False),
        sa.Column('model', sa.String(100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    for table in ['feedback', 'results', 'answers', 'questions', 'assessment_attempts',
                  'oauth_states', 'login_sessions', 'login_events']:
        op.drop_table(table)
    # User identity records are deliberately preserved, including on fresh installs.
