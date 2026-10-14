import jwt
from fastapi import APIRouter, Depends, HTTPException
from backend.app.core.config import get_settings
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import httpx
from backend.app.models.user import User
from backend.app.db.session import get_db_session

router = APIRouter()
settings=get_settings()
GOOGLE_REDIRECT_URI = settings.google_auth.GOOGLE_REDIRECT_URI
google_setting=settings.google_auth
@router.get("/login")
def login_url():
    """Returns Google consent URL."""
    return {
        "url": (
            "https://accounts.google.com/o/oauth2/v2/auth"
            f"?client_id={google_setting.GOOGLE_CLIENT_ID}"
            f"&redirect_uri={GOOGLE_REDIRECT_URI}"
            "&response_type=code"
            "&scope=openid email profile"
        )
    }


@router.get("/authenticate")
async def authenticate(code: str, db: AsyncSession = Depends(get_db_session)):
    """Exchanges code for Google user profile and syncs user with DB."""
    # 1. Exchange code for tokens
    async with httpx.AsyncClient() as client:
        res = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": google_setting.GOOGLE_CLIENT_ID,
                "client_secret": google_setting.GOOGLE_CLIENT_SECRET,
                "redirect_uri": GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )
    
    if res.status_code != 200:
        raise HTTPException(status_code=400, detail="Invalid Google OAuth code")
    # 2. Extract profile directly from id_token (no second HTTP request needed!)
    id_token = res.json().get("id_token")
    user_info = jwt.decode(id_token, options={"verify_signature": False})
    google_sub = user_info["sub"]
    email = user_info["email"]
    name = user_info.get("name", "")
    # 3. Get or create user
    user = (await db.execute(select(User).where(User.google_sub == google_sub))).scalar_one_or_none()
    if not user:
        user = User(google_sub=google_sub, email=email, name=name)
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return {"id": user.id, "email": user.email, "name": user.name}