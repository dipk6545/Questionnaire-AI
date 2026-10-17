"""Authenticated, owner-scoped assessment persistence and results."""
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.api.v1.endpoints.auth import current_user
from backend.app.core.config import get_settings
from backend.app.db.session import get_db_session
from backend.app.models.user import User
from backend.app.models.examination import AssessmentAttempt, Question, Answer, Result, Feedback, now
from backend.app.schemas.examination import ExamRequest, AnswerBatch
from backend.app.services import examination as service

router = APIRouter()


async def owned(db, attempt_id, user_id, lock=False):
    query = select(AssessmentAttempt).where(AssessmentAttempt.id == attempt_id,
                                           AssessmentAttempt.user_id == user_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    attempt = await db.scalar(query)
    if not attempt:
        raise HTTPException(404, 'Assessment not found.')
    return attempt


async def question_rows(db, attempt_id):
    return list((await db.scalars(select(Question).where(Question.attempt_id == attempt_id)
                                 .order_by(Question.position))).all())


async def exam_view(db, attempt):
    answers = {a.question_id: a.selected_options for a in (await db.scalars(
        select(Answer).where(Answer.attempt_id == attempt.id))).all()}
    return {'id': attempt.id, 'subject': attempt.subject, 'level': attempt.level,
            'status': attempt.status, 'created_at': attempt.created_at,
            'questions': [{'id': q.id, 'text': q.text, 'topic': q.topic,
                           'kind': q.kind, 'options': q.options,
                           'selected_options': answers.get(q.id, [])}
                          for q in await question_rows(db, attempt.id)]}


async def result_view(db, attempt):
    result = await db.get(Result, attempt.id)
    feedback = await db.get(Feedback, attempt.id)
    answers = {a.question_id: a for a in (await db.scalars(
        select(Answer).where(Answer.attempt_id == attempt.id))).all()}
    return {'id': attempt.id, 'subject': attempt.subject, 'level': attempt.level,
            'score': result.score, 'total': result.total, 'percentage': result.percentage,
            'submitted_at': attempt.submitted_at,
            'feedback_status': 'ready' if feedback else 'pending',
            'feedback': {k: getattr(feedback, k) for k in
                         ['summary', 'strengths', 'focus_areas', 'next_steps']} if feedback else None,
            'review': [{'id': q.id, 'text': q.text, 'topic': q.topic, 'kind': q.kind,
                        'options': q.options, 'correct_options': q.correct_options,
                        'explanation': q.explanation, 'selected_options': answers[q.id].selected_options,
                        'correct': answers[q.id].correct} for q in await question_rows(db, attempt.id)]}


async def ensure_feedback(db, attempt_id, user_id):
    # The score is already committed. Serialize feedback retries across workers.
    attempt = await owned(db, attempt_id, user_id, lock=True)
    if not await db.get(Feedback, attempt_id):
        view = await result_view(db, attempt)
        try:
            generated = await service.generate_feedback(attempt.subject, attempt.level,
                                                        view['score'], view['review'])
        except HTTPException:
            await db.rollback()
            return  # Pending feedback can be retried without losing the student's marks.
        db.add(Feedback(attempt_id=attempt_id, model=get_settings().OPENAI_MODEL, **generated.model_dump()))
    await db.commit()


@router.get('/options')
async def options(user: User = Depends(current_user)):
    return {'subjects': list(service.SUBJECT_TOPICS), 'levels': service.LEVELS, 'question_count': 5}


@router.get('/history')
async def history(user: User = Depends(current_user), db: AsyncSession = Depends(get_db_session)):
    rows = (await db.execute(select(AssessmentAttempt, Result).outerjoin(
        Result, Result.attempt_id == AssessmentAttempt.id).where(AssessmentAttempt.user_id == user.id)
        .order_by(AssessmentAttempt.created_at.desc()).limit(50))).all()
    summary = (await db.execute(select(AssessmentAttempt.subject, AssessmentAttempt.level,
        func.count(Result.attempt_id), func.avg(Result.percentage), func.max(Result.percentage))
        .join(Result, Result.attempt_id == AssessmentAttempt.id).where(AssessmentAttempt.user_id == user.id)
        .group_by(AssessmentAttempt.subject, AssessmentAttempt.level))).all()
    return {'attempts': [{'id': a.id, 'subject': a.subject, 'level': a.level, 'status': a.status,
                          'created_at': a.created_at, 'percentage': r.percentage if r else None}
                         for a, r in rows],
            'performance': [{'subject': s, 'level': l, 'attempts': n, 'average': round(avg, 1), 'best': best}
                            for s, l, n, avg, best in summary]}


@router.post('')
async def create_exam(request: ExamRequest, user: User = Depends(current_user),
                      db: AsyncSession = Depends(get_db_session)):
    attempt_id, user_id = str(request.request_id), user.id
    # Serialize creation per user, including identical request IDs.
    await db.scalar(select(User).where(User.id == user_id).with_for_update())
    existing = await db.get(AssessmentAttempt, attempt_id)
    if existing:
        if existing.user_id != user_id:
            raise HTTPException(404, 'Assessment not found.')
        if existing.subject != request.subject or existing.level != request.level:
            raise HTTPException(409, 'This request ID belongs to another assessment.')
        if existing.status in ('in_progress', 'submitted'):
            return await exam_view(db, existing)
        raise HTTPException(409, 'This request is generating or has failed. Check history before retrying.')
    await db.execute(update(AssessmentAttempt).where(
        AssessmentAttempt.user_id == user_id, AssessmentAttempt.status == 'generating',
        AssessmentAttempt.created_at < now() - timedelta(minutes=10)).values(status='failed'))
    active = await db.scalar(select(func.count()).select_from(AssessmentAttempt).where(
        AssessmentAttempt.user_id == user_id, AssessmentAttempt.status.in_(['generating', 'in_progress'])))
    if active >= 3:
        raise HTTPException(429, 'Resume or discard an unfinished assessment before starting another.')
    attempt = AssessmentAttempt(id=attempt_id, user_id=user_id, subject=request.subject,
                                level=request.level, model=get_settings().OPENAI_MODEL,
                                prompt_version=service.PROMPT_VERSION, status='generating')
    db.add(attempt)
    await db.commit()
    try:
        generated = await service.generate_questions(request.subject, request.level)
        attempt = await owned(db, attempt_id, user_id, lock=True)
        if attempt.status != 'generating':
            raise HTTPException(409, 'This assessment was discarded during generation.')
        for position, q in enumerate(generated.questions):
            db.add(Question(attempt_id=attempt_id, position=position, **q.model_dump()))
        attempt.status = 'in_progress'
        await db.commit()
    except Exception:
        await db.rollback()
        await db.execute(update(AssessmentAttempt).where(
            AssessmentAttempt.id == attempt_id, AssessmentAttempt.status == 'generating').values(status='failed'))
        await db.commit()
        raise
    return await exam_view(db, attempt)


@router.get('/{attempt_id}')
async def get_exam(attempt_id: str, user: User = Depends(current_user),
                   db: AsyncSession = Depends(get_db_session)):
    return await exam_view(db, await owned(db, attempt_id, user.id))


async def save_batch(db, attempt, batch, submitting=False):
    questions = {q.id: q for q in await question_rows(db, attempt.id)}
    if submitting and {a.question_id for a in batch.answers} != set(questions):
        raise HTTPException(422, 'Answer every question before submitting.')
    stored = {a.question_id: a for a in (await db.scalars(
        select(Answer).where(Answer.attempt_id == attempt.id))).all()}
    for item in batch.answers:
        q = questions.get(item.question_id)
        if not q or (q.kind == 'single' and len(item.selected_options) > 1):
            raise HTTPException(422, 'Invalid question or selection count.')
        if submitting and not item.selected_options:
            raise HTTPException(422, 'Answer every question before submitting.')
        answer = stored.get(q.id)
        if not answer:
            answer = Answer(attempt_id=attempt.id, question_id=q.id)
            db.add(answer)
        answer.selected_options = sorted(item.selected_options)
        answer.correct = service.score_selection(item.selected_options, q.correct_options) if submitting else None
    await db.flush()


@router.put('/{attempt_id}/answers')
async def save_answers(attempt_id: str, batch: AnswerBatch, user: User = Depends(current_user),
                       db: AsyncSession = Depends(get_db_session)):
    attempt = await owned(db, attempt_id, user.id, lock=True)
    if attempt.status != 'in_progress':
        raise HTTPException(409, 'Only an active assessment can be edited.')
    await save_batch(db, attempt, batch)
    await db.commit()
    return {'saved': True}


@router.post('/{attempt_id}/submit')
async def submit(attempt_id: str, batch: AnswerBatch, user: User = Depends(current_user),
                 db: AsyncSession = Depends(get_db_session)):
    user_id = user.id
    attempt = await owned(db, attempt_id, user_id, lock=True)
    if attempt.status == 'submitted':
        saved = {a.question_id: sorted(a.selected_options) for a in (await db.scalars(
            select(Answer).where(Answer.attempt_id == attempt_id))).all()}
        if {a.question_id: sorted(a.selected_options) for a in batch.answers} != saved:
            raise HTTPException(409, 'This assessment has already been submitted with different answers.')
        await db.commit()
    elif attempt.status == 'in_progress':
        await save_batch(db, attempt, batch, submitting=True)
        score = await db.scalar(select(func.count()).select_from(Answer).where(
            Answer.attempt_id == attempt_id, Answer.correct.is_(True)))
        db.add(Result(attempt_id=attempt_id, score=score, total=5, percentage=score * 20))
        attempt.status, attempt.submitted_at = 'submitted', now()
        await db.commit()
    else:
        raise HTTPException(409, 'This assessment is not available for submission.')
    await ensure_feedback(db, attempt_id, user_id)
    return await result_view(db, await owned(db, attempt_id, user_id))


@router.get('/{attempt_id}/result')
async def get_result(attempt_id: str, user: User = Depends(current_user),
                     db: AsyncSession = Depends(get_db_session)):
    attempt = await owned(db, attempt_id, user.id)
    if attempt.status != 'submitted':
        raise HTTPException(409, 'Submit the assessment to view answers and results.')
    return await result_view(db, attempt)


@router.post('/{attempt_id}/feedback')
async def retry_feedback(attempt_id: str, user: User = Depends(current_user),
                         db: AsyncSession = Depends(get_db_session)):
    user_id = user.id
    attempt = await owned(db, attempt_id, user_id)
    if attempt.status != 'submitted':
        raise HTTPException(409, 'Submit the assessment first.')
    await db.commit()
    await ensure_feedback(db, attempt_id, user_id)
    return await result_view(db, await owned(db, attempt_id, user_id))


@router.post('/{attempt_id}/discard')
async def discard(attempt_id: str, user: User = Depends(current_user),
                  db: AsyncSession = Depends(get_db_session)):
    attempt = await owned(db, attempt_id, user.id, lock=True)
    if attempt.status == 'submitted':
        raise HTTPException(409, 'Submitted results cannot be discarded.')
    attempt.status = 'discarded'
    await db.commit()
    return {'ok': True}
