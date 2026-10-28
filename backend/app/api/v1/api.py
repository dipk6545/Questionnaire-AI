from fastapi import APIRouter
from app.api.v1.endpoints import auth, examinations


api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(examinations.router, prefix="/examinations", tags=["Examinations"])
