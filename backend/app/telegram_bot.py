"""
Clean, Editable Assessment Card for Telegram with:
1. No native poll percentage bars or 'View Votes' clutter.
2. Full in-place editing of selections before submission (toggle/change freely).
3. Previous / Next navigation between questions.
4. Dedicated 'Submit Assessment' button for final evaluation.
5. Full PostgreSQL persistence on submission.
"""
import os
import uuid
import asyncio
import logging
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from sqlalchemy import select, func
from dotenv import load_dotenv

load_dotenv()

from app.db.session import SessionLocal
from app.models.user import User
from app.models.question_bank import QuestionBank
from app.models.examination import AssessmentAttempt, Question, Answer, Result, now
from app.services.examination import SUBJECT_TOPICS, score_selection

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

USER_SESSIONS = {}
CHAT_MESSAGES = {}


def track_msg(chat_id: int, msg_id: int):
    if chat_id not in CHAT_MESSAGES:
        CHAT_MESSAGES[chat_id] = []
    CHAT_MESSAGES[chat_id].append(msg_id)


async def get_or_create_user(telegram_user) -> User:
    t_id = str(telegram_user.id)
    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.telegram_id == t_id))
        if not user:
            display_name = telegram_user.full_name or telegram_user.username or "Student"
            user = User(
                email=f"tg_{t_id}@telegram.user",
                name=display_name,
                telegram_id=t_id,
                google_sub=f"tg_{t_id}"
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
        return user


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    t_id = str(update.effective_user.id)
    USER_SESSIONS.pop(t_id, None)

    msg_ids = CHAT_MESSAGES.get(chat_id, [])
    msg_ids.append(update.message.message_id)

    for mid in set(msg_ids):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            pass

    CHAT_MESSAGES[chat_id] = []
    sent = await update.effective_chat.send_message(
        "🧹 *Chat cleaned!*\n\nType /start to begin an assessment.",
        parse_mode="Markdown"
    )
    track_msg(chat_id, sent.message_id)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_or_create_user(update.effective_user)
    chat_id = update.effective_chat.id
    track_msg(chat_id, update.message.message_id)

    welcome_text = (
        f"👋 *Welcome to Questionnaire AI, {user.name}!*\n\n"
        "• Select and change your answers freely before submitting.\n"
        "• Navigate across questions using Previous / Next.\n"
        "• Tap 'Submit Assessment' when you are ready for evaluation.\n\n"
        "Select a subject to begin:"
    )

    subjects = list(SUBJECT_TOPICS.keys())
    keyboard = []
    for i in range(0, len(subjects), 2):
        row = [InlineKeyboardButton(subjects[i], callback_data=f"sub_{subjects[i]}")]
        if i + 1 < len(subjects):
            row.append(InlineKeyboardButton(subjects[i + 1], callback_data=f"sub_{subjects[i + 1]}"))
        keyboard.append(row)

    msg = await update.message.reply_text(
        welcome_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    track_msg(chat_id, msg.message_id)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    # Fire acknowledge in background to eliminate latency on every button click
    asyncio.create_task(query.answer())
    data = query.data
    chat_id = update.effective_chat.id
    t_id = str(update.effective_user.id)

    # 1. Subject Selected
    if data.startswith("sub_"):
        subject = data.replace("sub_", "")
        text = f"Subject: *{subject}*\n\nSelect difficulty:"
        keyboard = [
            [
                InlineKeyboardButton("🟢 Easy", callback_data=f"lvl_{subject}_Easy"),
                InlineKeyboardButton("🟡 Medium", callback_data=f"lvl_{subject}_Medium"),
                InlineKeyboardButton("🔴 Hard", callback_data=f"lvl_{subject}_Hard"),
            ],
            [InlineKeyboardButton("⬅️ Back to Subjects", callback_data="back_start")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    # 2. Level Selected -> Immediate Eye-Catching Loader -> Query DB
    if data.startswith("lvl_"):
        _, subject, level = data.split("_")
        
        # Immediate eye-catching visual feedback
        loader_text = (
            f"⚡ *Preparing Your Assessment...*\n\n"
            f"🎯 Subject: *{subject}*\n"
            f"📊 Difficulty: *{level}*\n"
            f"🎲 _Drawing 5 curated questions from question bank..._"
        )
        try:
            await query.edit_message_text(loader_text, parse_mode="Markdown")
        except Exception:
            pass

        user = await get_or_create_user(update.effective_user)

        async with SessionLocal() as db:
            q_rows = (await db.scalars(
                select(QuestionBank)
                .where(QuestionBank.subject == subject, QuestionBank.level == level)
                .order_by(func.random())
                .limit(5)
            )).all()

            if len(q_rows) < 5:
                q_rows = (await db.scalars(
                    select(QuestionBank)
                    .where(QuestionBank.subject == subject)
                    .order_by(func.random())
                    .limit(5)
                )).all()

            if not q_rows:
                await query.edit_message_text(
                    f"⚠️ No questions found for {subject}.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Subjects", callback_data="back_start")]])
                )
                return

            attempt_id = str(uuid.uuid4())
            attempt = AssessmentAttempt(
                id=attempt_id,
                user_id=user.id,
                subject=subject,
                level=level,
                model="codestral-latest",
                status="in_progress"
            )
            db.add(attempt)

            new_questions = []
            for idx, q_bank in enumerate(q_rows):
                q = Question(
                    attempt_id=attempt_id,
                    position=idx,
                    topic=q_bank.topic,
                    kind=q_bank.kind or 'single',
                    text=q_bank.text,
                    options=q_bank.options,
                    correct_options=q_bank.correct_options or [0],
                    explanation=q_bank.explanation or ''
                )
                db.add(q)
                new_questions.append(q)

            # Single network trip: flush populates autoincrement IDs directly onto the python objects
            await db.flush()

            questions_in_memory = [{
                'id': q.id,
                'text': q.text,
                'topic': q.topic,
                'options': q.options,
                'correct_options': q.correct_options or [0],
                'explanation': q.explanation or ''
            } for q in new_questions]

            await db.commit()

        USER_SESSIONS[t_id] = {
            "attempt_id": attempt_id,
            "subject": subject,
            "level": level,
            "chat_id": chat_id,
            "message_id": query.message.message_id,
            "questions": questions_in_memory,
            "current_idx": 0,
            "answers": {}  # q_id -> [selected_index]
        }

        await render_card(query, t_id)
        return

    # 3. Option Selection / Decision Change
    if data.startswith("opt_"):
        _, opt_idx_str = data.split("_")
        opt_idx = int(opt_idx_str)

        session = USER_SESSIONS.get(t_id)
        if not session:
            await query.edit_message_text("Session expired. Type /start to take a new test.")
            return

        current_idx = session["current_idx"]
        q = session["questions"][current_idx]
        q_id = q["id"]

        # Toggle or switch answer based on question kind
        current_selection = list(session["answers"].get(q_id, []))
        if q.get("kind") == "multiple":
            if opt_idx in current_selection:
                current_selection.remove(opt_idx)
            else:
                current_selection.append(opt_idx)
            session["answers"][q_id] = sorted(current_selection)
        else:
            if opt_idx in current_selection:
                session["answers"][q_id] = []
            else:
                session["answers"][q_id] = [opt_idx]

        await render_card(query, t_id)
        return

    # 4. Navigation
    if data.startswith("nav_"):
        action = data.replace("nav_", "")
        session = USER_SESSIONS.get(t_id)
        if not session:
            return

        if action == "prev" and session["current_idx"] > 0:
            session["current_idx"] -= 1
        elif action == "next" and session["current_idx"] < len(session["questions"]) - 1:
            session["current_idx"] += 1

        await render_card(query, t_id)
        return

    # 5. Submit Assessment
    if data == "submit_exam":
        session = USER_SESSIONS.get(t_id)
        if not session:
            return
        await submit_and_evaluate(query, t_id)
        return

    if data == "back_start":
        subjects = list(SUBJECT_TOPICS.keys())
        keyboard = []
        for i in range(0, len(subjects), 2):
            row = [InlineKeyboardButton(subjects[i], callback_data=f"sub_{subjects[i]}")]
            if i + 1 < len(subjects):
                row.append(InlineKeyboardButton(subjects[i + 1], callback_data=f"sub_{subjects[i + 1]}"))
            keyboard.append(row)
        await query.edit_message_text(
            "👋 Choose a subject to begin:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return


async def render_card(query, t_id: str):
    """Renders the question and options cleanly with radio states."""
    session = USER_SESSIONS[t_id]
    idx = session["current_idx"]
    total = len(session["questions"])
    q = session["questions"][idx]
    q_id = q["id"]
    selected = session["answers"].get(q_id, [])

    letters = ["A", "B", "C", "D", "E"]
    is_mult = (q.get("kind") == "multiple")
    badge = "📌 Select all that apply" if is_mult else "🔘 Single Choice"
    
    header = f"📝 *Question {idx + 1} of {total}* • {badge}\n_{session['subject']} • {q['topic']}_\n\n"
    question_body = f"*{q['text']}*\n\n"

    keyboard = []
    for i, opt_text in enumerate(q["options"]):
        lbl = letters[i] if i < len(letters) else str(i + 1)
        is_chosen = (i in selected)
        if is_mult:
            icon = "☑️" if is_chosen else "⬜"
        else:
            icon = "🔘" if is_chosen else "⚪"
        btn_text = f"{icon} {lbl}. {opt_text}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"opt_{i}")])

    # Navigation Controls
    nav_row = []
    if idx > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data="nav_prev"))
    if idx < total - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data="nav_next"))

    if nav_row:
        keyboard.append(nav_row)

    # Submit Button (always visible or highlighted when all answered)
    answered_count = len([k for k, v in session["answers"].items() if len(v) > 0])
    submit_label = f"✅ Submit Assessment ({answered_count}/{total})"
    keyboard.append([InlineKeyboardButton(submit_label, callback_data="submit_exam")])

    full_message = header + question_body + "_Tap an option to select or change your answer._"

    try:
        await query.edit_message_text(
            full_message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    except Exception:
        # Ignore telegram unmodified message warnings
        pass


async def submit_and_evaluate(query, t_id: str):
    """Evaluates attempt, saves Result in DB, and outputs scorecard."""
    session = USER_SESSIONS.pop(t_id, None)
    if not session:
        return

    attempt_id = session["attempt_id"]
    questions = session["questions"]
    user_answers = session["answers"]

    score = 0
    breakdown = []
    letters = ["A", "B", "C", "D", "E"]

    async with SessionLocal() as db:
        attempt = await db.get(AssessmentAttempt, attempt_id)

        for idx, q in enumerate(questions):
            q_id = q["id"]
            selected = sorted(user_answers.get(q_id, []))
            correct = sorted(q["correct_options"] or [])
            is_correct = score_selection(selected, correct)

            if is_correct:
                score += 1
                icon = "✅"
            else:
                icon = "❌"

            # Save Answer in DB
            db.add(Answer(
                attempt_id=attempt_id,
                question_id=q_id,
                selected_options=selected,
                correct=is_correct
            ))

            sel_text = ", ".join([letters[i] for i in selected if i < len(letters)]) or "Skipped"
            cor_text = ", ".join([letters[i] for i in correct if i < len(letters)]) or "N/A"

            breakdown.append(
                f"*{idx + 1}.* {icon} Selected: `{sel_text}` | Correct: `{cor_text}`\n"
                f"💡 _{q['explanation']}_\n"
            )

        total = len(questions)
        percentage = (score / total) * 100

        # Save Result in DB
        db.add(Result(attempt_id=attempt_id, score=score, total=total, percentage=percentage))
        attempt.status = "submitted"
        attempt.submitted_at = now()
        await db.commit()

    scorecard = (
        f"🎯 *Assessment Evaluated & Saved!*\n\n"
        f"Subject: *{session['subject']} ({session['level']})*\n"
        f"Final Score: *{score} / {total}* ({percentage:.0f}%)\n\n"
        f"📋 *Summary & Review:*\n\n"
        + "\n".join(breakdown) +
        f"\nType /start to begin a new test or /clear to clean the chat."
    )

    keyboard = [[InlineKeyboardButton("🔄 Take Another Assessment", callback_data="back_start")]]
    await query.edit_message_text(scorecard, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


def main():
    if not BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not found!")
        return

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .connection_pool_size(8)
        .build()
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CallbackQueryHandler(handle_callback))

    print("Clean Editable Exam Card Bot is active...")
    app.run_polling(poll_interval=0.0, timeout=10)


if __name__ == "__main__":
    main()
