"""
High-throughput parallel question generation script using Mistral's Codestral.
Features:
- Parallel worker tasks generating across distinct topic clusters via asyncio.gather().
- Target ~100 questions generated and saved in under 15-20 seconds.
- Zero duplication: SHA-256 hash checking + topic partitioning + anti-duplication constraints.
- Strict no-repeat: Excludes previous run's subject & level combination.
"""
import os
import sys
import json
import random
import hashlib
import asyncio
import logging
from pathlib import Path
from dotenv import load_dotenv
import httpx

# Ensure backend root is on sys.path
backend_root = Path(__file__).resolve().parents[1]
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))

env_path = backend_root.parent / '.env'
load_dotenv(env_path)

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from pydantic import BaseModel, Field

from app.models.user import Base
from app.models.question_bank import QuestionBank
from app.services.examination import SUBJECT_TOPICS, LEVELS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seed_questions")


class SingleGeneratedQuestion(BaseModel):
    topic: str
    kind: str = Field(description="Must be 'single' or 'multiple'")
    text: str = Field(description="Question body")
    options: list[str] = Field(description="Exactly 4 distinct choices")
    correct_options: list[int] = Field(description="Zero-based indices of correct options (e.g. [0] or [1, 2])")
    explanation: str = Field(description="Concise 1-2 sentence technical explanation")


class BatchQuestionSet(BaseModel):
    questions: list[SingleGeneratedQuestion]


def normalize_hash(text: str) -> str:
    """Produces a deterministic SHA-256 hash of normalized text for deduplication."""
    clean = " ".join(text.lower().strip().split())
    return hashlib.sha256(clean.encode('utf-8')).hexdigest()


def get_db_url():
    url = os.getenv("DATABASE_URL") or os.getenv("LAYER_BASE_CONNECTION_STRING", "")
    url = url.strip().strip('\\').strip('/')
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if "sslmode=" in url:
        url = url.replace("sslmode=", "ssl=")
    return url


async def pick_subject_and_level(session: AsyncSession):
    """
    Picks a subject and level:
    1. Strictly excludes the combination used in the previous run.
    2. Weights remaining combinations so less-populated pairs are favored.
    """
    subjects = list(SUBJECT_TOPICS.keys())
    levels = list(LEVELS)
    all_pairs = [(s, l) for s in subjects for l in levels]

    last_res = await session.execute(
        select(QuestionBank.subject, QuestionBank.level)
        .order_by(QuestionBank.created_at.desc(), QuestionBank.id.desc())
        .limit(1)
    )
    last_row = last_res.fetchone()
    last_pair = (last_row[0], last_row[1]) if last_row else None

    candidate_pairs = [p for p in all_pairs if p != last_pair]
    if last_pair:
        logger.info("Previous run combination was: %s [%s] (strictly excluded for this run)", last_pair[0], last_pair[1])

    result = await session.execute(
        select(QuestionBank.subject, QuestionBank.level, func.count(QuestionBank.id))
        .group_by(QuestionBank.subject, QuestionBank.level)
    )
    counts = {(row[0], row[1]): row[2] for row in result.fetchall()}

    max_c = max(counts.values()) if counts else 0
    weights = [max_c - counts.get(p, 0) + 1 for p in candidate_pairs]

    chosen_subject, chosen_level = random.choices(candidate_pairs, weights=weights, k=1)[0]
    logger.info("Selected target: %s [%s] (Existing in bank: %d)",
                chosen_subject, chosen_level, counts.get((chosen_subject, chosen_level), 0))
    return chosen_subject, chosen_level


async def generate_topic_cluster(
    client: httpx.AsyncClient,
    api_key: str,
    subject: str,
    level: str,
    topics: list[str],
    count: int,
    existing_samples: list[str]
) -> list[SingleGeneratedQuestion]:
    """Generates a batch of questions focused on a specific cluster of topics."""
    prompt = (
        f"You are a principal technical examiner creating practical, rigorous MCQs.\\n"
        f"Subject: {subject}\\n"
        f"Difficulty Level: {level}\\n"
        f"Specific Focus Topics: {', '.join(topics)}\\n\\n"
        f"Generate exactly {count} distinct multiple-choice questions evenly distributed across the focus topics.\\n"
        f"Rules:\\n"
        f"1. Exactly 4 plausible options per question.\\n"
        f"2. ~80% 'single' (1 correct option), ~20% 'multiple' (2-3 correct options).\\n"
        f"3. Zero-based option indices for correct_options.\\n"
        f"4. Realistic technical scenarios and tradeoffs appropriate for {level}.\\n"
        f"5. Keep explanations concise (1-2 crisp sentences).\\n"
        f"6. Do NOT duplicate or rephrase any of these existing stems:\\n"
        f"   {json.dumps(existing_samples[:25])}\\n\\n"
        f"Output ONLY valid JSON adhering to this schema:\\n"
        f'{{"questions": [{{"topic": "...", "kind": "single|multiple", "text": "...", "options": ["A","B","C","D"], "correct_options": [0], "explanation": "..."}}]}}'
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "codestral-latest",
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0.7,
        "max_tokens": 8000
    }

    try:
        resp = await client.post("https://api.mistral.ai/v1/chat/completions", json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        parsed = BatchQuestionSet.model_validate_json(content)
        return parsed.questions
    except Exception as e:
        logger.error("Error in topic cluster worker (%s): %s", topics, e)
        return []


async def run_pipeline():
    target_count = int(os.getenv("QUESTION_GENERATION_BATCH_SIZE", "100"))
    logger.info("Starting high-speed parallel question generation for %d questions...", target_count)

    mistral_api_key = os.getenv("MISTRAL_API_KEY", os.getenv('MISTRAL_API_KEY'))
    db_url = get_db_url()

    engine = create_async_engine(
        db_url,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={"statement_cache_size": 0}
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with SessionLocal() as session:
        subject, level = await pick_subject_and_level(session)

        # 1. Fetch existing hashes to guarantee 0 duplicates
        res = await session.execute(
            select(QuestionBank.hash_id, QuestionBank.text)
            .where(QuestionBank.subject == subject, QuestionBank.level == level)
        )
        existing_rows = res.fetchall()
        existing_hashes = {r[0] for r in existing_rows}
        existing_texts = [r[1][:80] for r in existing_rows]
        logger.info("Found %d existing questions for %s/%s in DB.", len(existing_hashes), subject, level)

        # 2. Partition topics across 4 parallel workers
        all_topics = SUBJECT_TOPICS[subject]
        num_workers = 4
        chunk_size = (target_count + num_workers - 1) // num_workers  # e.g. 25 per worker

        # Split topics evenly among workers
        topic_splits = [[] for _ in range(num_workers)]
        for i, t in enumerate(all_topics):
            topic_splits[i % num_workers].append(t)

        logger.info("Launching %d parallel workers generating ~%d questions each across distinct topic partitions...",
                    num_workers, chunk_size)

        async with httpx.AsyncClient() as client:
            tasks = [
                generate_topic_cluster(
                    client=client,
                    api_key=mistral_api_key,
                    subject=subject,
                    level=level,
                    topics=topic_splits[i] if topic_splits[i] else all_topics,
                    count=chunk_size,
                    existing_samples=existing_texts
                )
                for i in range(num_workers)
            ]
            results = await asyncio.gather(*tasks)

        # 3. Flatten, validate, deduplicate
        all_candidates = [q for batch in results for q in batch]
        logger.info("Parallel workers returned %d raw candidate questions. Processing deduplication...", len(all_candidates))

        seen_hashes = set(existing_hashes)
        unique_records = []

        for q in all_candidates:
            if not q.text or len(q.options) != 4 or not q.correct_options:
                continue

            h = normalize_hash(q.text)
            # Filter intra-batch duplicates and database-existing duplicates
            if h in seen_hashes:
                continue

            seen_hashes.add(h)
            unique_records.append({
                "hash_id": h,
                "subject": subject,
                "level": level,
                "topic": q.topic,
                "kind": q.kind,
                "text": q.text,
                "options": q.options,
                "correct_options": q.correct_options,
                "explanation": q.explanation,
                "model": "codestral-latest"
            })

            if len(unique_records) >= target_count:
                break

        if unique_records:
            # Use PostgreSQL ON CONFLICT (hash_id) DO NOTHING for bulletproof concurrency
            insert_stmt = (
                pg_insert(QuestionBank)
                .values(unique_records)
                .on_conflict_do_nothing(index_elements=['hash_id'])
            )
            await session.execute(insert_stmt)
            await session.commit()
            logger.info("SUCCESS: Persisted %d unique questions into question_bank table!", len(unique_records))
        else:
            logger.warning("No new unique questions could be persisted from this run.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_pipeline())
