from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.app.api.v1.api import api_router
from backend.app.models.user import Base
from backend.app.db.session import async_engine

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Automatically create tables if they do not exist
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(
    title="Questionnaire AI API",
    description="Backend API for AI-powered MCQ generation and answer evaluation",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust for production frontend origins
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