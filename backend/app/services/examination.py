"""Subject-scoped LLM generation, independent key review, and performance feedback."""
import json
import asyncio
import logging
from openai import AsyncOpenAI, OpenAIError, AuthenticationError, RateLimitError
from groq import AsyncGroq, GroqError
from fastapi import HTTPException
from pydantic import ValidationError
from app.core.config import get_settings
from app.schemas.examination import QuestionSet, GenerationPlan, ReviewPlan, PerformanceFeedback

SUBJECT_TOPICS = {
    'SQL': ['joins', 'aggregation', 'window functions', 'transactions', 'indexes', 'query plans', 'data modeling'],
    'Python': ['data structures', 'iteration', 'exceptions', 'typing', 'concurrency', 'testing', 'profiling'],
    'Statistics': ['probability', 'sampling', 'inference', 'hypothesis tests', 'regression', 'experimental design'],
    'Data Science': ['data quality', 'exploratory analysis', 'feature engineering', 'leakage', 'experimentation', 'metrics'],
    'Machine Learning': ['supervised learning', 'validation', 'regularization', 'model selection', 'imbalance', 'drift'],
    'Deep Learning': ['backpropagation', 'optimization', 'architectures', 'generalization', 'training stability', 'inference'],
    'MLOps': ['pipelines', 'versioning', 'deployment', 'monitoring', 'rollback', 'reproducibility', 'serving reliability'],
    'AI Engineering': ['LLM integration', 'RAG', 'retrieval', 'evaluation', 'tool calling', 'prompt injection', 'latency and cost'],
}
LEVELS = ['Easy', 'Medium', 'Hard', 'Production grade']
PROMPT_VERSION = 'professional-v2'
logger = logging.getLogger(__name__)


class InvalidAssessment(HTTPException):
    def __init__(self, reason, candidate=None):
        self.reason = reason
        self.candidate = candidate
        super().__init__(502, 'The generated questions did not pass validation. Please retry.')


async def _groq_completion(schema, instructions, payload, settings):
    logger.info("Attempting completion using fallback Groq model: %s", settings.GROQ_MODEL)
    try:
        client = AsyncGroq(api_key=settings.GROQ_API_KEY)
        schema_json = json.dumps(schema.model_json_schema())
        system_prompt = (
            f"{instructions}\n\n"
            f"You MUST output valid JSON strictly conforming to this JSON Schema:\n{schema_json}"
        )
        response = await client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload)}
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
        )
        raw_content = response.choices[0].message.content
        return schema.model_validate_json(raw_content)
    except Exception as e:
        logger.error("Groq fallback failed: %s", e)
        raise HTTPException(502, f"Groq fallback generation failed: {e}")


_OPENAI_FAILED = False

async def completion(schema, instructions, payload, direct_groq=False):
    global _OPENAI_FAILED
    settings = get_settings()
    has_openai = bool(settings.OPENAI_API_KEY and not settings.OPENAI_API_KEY.startswith('your_')) and not _OPENAI_FAILED and not direct_groq
    has_groq = bool(settings.GROQ_API_KEY and not settings.GROQ_API_KEY.startswith('your_'))

    if not has_openai and not has_groq:
        raise HTTPException(503, 'Set OPENAI_API_KEY or GROQ_API_KEY in .env to generate assessments.')

    if has_openai:
        try:
            async with AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=55, max_retries=0) as client:
                response = await client.responses.parse(
                    model=settings.OPENAI_MODEL, instructions=instructions,
                    input=json.dumps(payload), text_format=schema, store=False,
                )
            if response.output_parsed is not None:
                return response.output_parsed
        except (AuthenticationError, RateLimitError, OpenAIError) as error:
            logger.warning("OpenAI failed (%s). Latching directly to Groq for all future requests.", type(error).__name__)
            _OPENAI_FAILED = True  # Never call OpenAI again in this process lifecycle
            if has_groq:
                return await _groq_completion(schema, instructions, payload, settings)
            if isinstance(error, AuthenticationError):
                raise HTTPException(503, 'OpenAI rejected API key. Check OPENAI_API_KEY or LAYER_BASE in .env.')
            if isinstance(error, RateLimitError):
                raise HTTPException(429, 'OpenAI rate limited or quota exceeded.')
            raise HTTPException(502, f'OpenAI error: {error}')
        except ValidationError as error:
            reason = '; '.join(f"{item['loc']}: {item['msg']}" for item in error.errors(include_input=False))[:2000]
            logger.warning('Rejected %s output: %s', schema.__name__, reason)
            raise InvalidAssessment(reason)
        except ValueError:
            raise InvalidAssessment('Response could not be parsed into the required schema.')

    if has_groq:
        return await _groq_completion(schema, instructions, payload, settings)

    raise HTTPException(503, 'No valid LLM provider available.')


def score_selection(selected: list, correct: list) -> bool:
    return sorted(selected or []) == sorted(correct or [])


async def generate_questions(subject: str, level: str) -> QuestionSet:
    instructions = f'Generate 5 rigorous multiple-choice questions for {subject} at {level} difficulty.'
    payload = {'subject': subject, 'level': level, 'topics': SUBJECT_TOPICS.get(subject, [])}
    return await completion(QuestionSet, instructions, payload)


async def generate_feedback(subject: str, level: str, score: int, review: list):
    instructions = f'Generate constructive feedback for a student who scored {score}/5 on a {level} level {subject} exam.'
    payload = {'subject': subject, 'level': level, 'score': score, 'review': review}
    return await completion(PerformanceFeedback, instructions, payload)
