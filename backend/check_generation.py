"""Explicit live OpenAI diagnostic; prints no credentials or generated answers."""
import asyncio
import sys
from unittest.mock import patch
from fastapi import HTTPException
from openai import AsyncOpenAI, APIStatusError
from pydantic import ValidationError
from backend.app.core.config import get_settings
from backend.app.services import examination


async def main():
    settings = get_settings()
    print('Configured key present:', bool(settings.OPENAI_API_KEY))
    print('Configured model:', settings.OPENAI_MODEL)
    async with AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=60, max_retries=0) as client:
        original = client.responses.parse

        async def traced(*args, **kwargs):
            try:
                return await original(*args, **kwargs)
            except APIStatusError as error:
                print('Provider HTTP status:', error.status_code)
                print('Provider error code:', error.code)
                print('Provider error parameter:', error.param)
                raise
            except ValidationError as error:
                print('Validation errors:', error.errors(include_input=False, include_context=False, include_url=False))
                raise

        client.responses.parse = traced
        # Reuse the configured client while running the exact production prompts/schema.
        class ClientContext:
            async def __aenter__(self):
                return client

            async def __aexit__(self, *args):
                return False

        with patch.object(examination, 'AsyncOpenAI', return_value=ClientContext()):
            try:
                generated = await examination.generate_questions(
                    sys.argv[1] if len(sys.argv) > 1 else 'SQL',
                    sys.argv[2] if len(sys.argv) > 2 else 'Medium')
                print('Generated and reviewed questions:', len(generated.questions))
                print('Multiple-answer questions:', sum(q.kind == 'multiple' for q in generated.questions))
            except HTTPException as error:
                print('Application status:', error.status_code)
                print('Application error:', error.detail)
                raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
