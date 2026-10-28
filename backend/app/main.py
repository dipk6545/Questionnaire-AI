import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1.api import api_router
from sqlalchemy import text
from app.db.session import async_engine

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Apply migrations explicitly with `python -m alembic upgrade head` first.
    async with async_engine.connect() as conn:
        await conn.execute(text('SELECT 1 FROM assessment_attempts LIMIT 1'))
    yield
    await async_engine.dispose()

app = FastAPI(
    title="Questionnaire AI API",
    description="Backend API for AI-powered MCQ generation and answer evaluation",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS configuration
allowed_origins = [
    "http://localhost:8501",
    "http://127.0.0.1:8501",
    "https://questionnaireai.netlify.app",
]

env_cors = os.getenv("CORS_ORIGINS", "")
if env_cors:
    if env_cors.strip() == "*":
        allowed_origins = ["*"]
    else:
        allowed_origins.extend([origin.strip() for origin in env_cors.split(",") if origin.strip()])

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")

@app.get("/", tags=["Health Check"])
async def root():
    return {
        "status": "online",
        "message": "Welcome to Questionnaire AI API"
    }

@app.get("/health", tags=["Health Check"])
async def health_check():
    return {"status": "healthy"}
