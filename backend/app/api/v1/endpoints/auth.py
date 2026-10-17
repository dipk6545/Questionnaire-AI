"""Google identity verification and revocable database sessions."""
import hashlib
import secrets
import time
from datetime import timedelta
from urllib.parse import urlencode
import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.core.config import get_settings
from backend.app.db.session import get_db_session
from backend.app.models.user import User
from backend.app.models.examination import LoginEvent, LoginSession, OAuthState, now

router = APIRouter()
security = HTTPBearer(auto_error=False)


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def profile(user):
    return {'id': user.id, 'name': user.name, 'email': user.email}


async def current_user(credentials: HTTPAuthorizationCredentials = Depends(security),
                       db: AsyncSession = Depends(get_db_session)):
    if credentials:
        user = await db.scalar(select(User).join(LoginSession, LoginSession.user_id == User.id).where(
            LoginSession.token_hash == digest(credentials.credentials), LoginSession.expires_at > now()))
        if user:
            return user
    raise HTTPException(401, 'Your session expired. Please sign in again.')


@router.get('/login')
async def login_url(db: AsyncSession = Depends(get_db_session)):
    settings = get_settings().google_auth
    if (not settings.GOOGLE_CLIENT_ID or settings.GOOGLE_CLIENT_ID.startswith('your_')
            or not settings.GOOGLE_CLIENT_SECRET or settings.GOOGLE_CLIENT_SECRET.startswith('your_')):
        raise HTTPException(503, 'Set real Google OAuth credentials in the root .env file.')
    await db.execute(delete(OAuthState).where(OAuthState.expires_at <= now()))
    state = secrets.token_urlsafe(32)
    db.add(OAuthState(token_hash=digest(state), expires_at=now() + timedelta(minutes=10)))
    await db.commit()
    return {'state': state, 'url': 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
        'client_id': settings.GOOGLE_CLIENT_ID, 'redirect_uri': settings.GOOGLE_REDIRECT_URI,
        'response_type': 'code', 'scope': 'openid email profile', 'state': state,
    })}


@router.get('/authenticate')
async def authenticate(code: str, state: str, db: AsyncSession = Depends(get_db_session)):
    consumed = await db.scalar(delete(OAuthState).where(
        OAuthState.token_hash == digest(state), OAuthState.expires_at > now()).returning(OAuthState.token_hash))
    await db.commit()
    if not consumed:
        raise HTTPException(400, 'Invalid or expired sign-in request. Please try again.')
    settings = get_settings().google_auth
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post('https://oauth2.googleapis.com/token', data={
                'code': code, 'client_id': settings.GOOGLE_CLIENT_ID,
                'client_secret': settings.GOOGLE_CLIENT_SECRET,
                'redirect_uri': settings.GOOGLE_REDIRECT_URI, 'grant_type': 'authorization_code',
            })
            if response.status_code != 200 or not response.json().get('id_token'):
                raise HTTPException(400, 'Google sign-in failed. Please try again.')
            # Google's tokeninfo endpoint verifies the signature; never trust an unsigned decode.
            verified = await client.get('https://oauth2.googleapis.com/tokeninfo',
                                        params={'id_token': response.json()['id_token']})
            if verified.status_code != 200:
                raise HTTPException(401, 'Google identity could not be verified.')
            info = verified.json()
        valid = (info.get('aud') == settings.GOOGLE_CLIENT_ID
                 and info.get('iss') in ('accounts.google.com', 'https://accounts.google.com')
                 and int(info.get('exp', 0)) > time.time()
                 and info.get('email_verified') in (True, 'true')
                 and info.get('sub') and info.get('email'))
    except (httpx.HTTPError, ValueError, TypeError):
        raise HTTPException(502, 'Google identity verification is unavailable. Please try again.')
    if not valid:
        raise HTTPException(401, 'Google identity could not be verified.')
    user = await db.scalar(select(User).where(User.google_sub == info['sub']))
    if not user:
        user = User(google_sub=info['sub'], email=info['email'], name=info.get('name', ''))
        db.add(user)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            user = await db.scalar(select(User).where(User.google_sub == info['sub']))
            if not user:
                raise HTTPException(409, 'This email is already registered to another identity.')
    token = secrets.token_urlsafe(32)
    db.add(LoginSession(token_hash=digest(token), user_id=user.id, expires_at=now() + timedelta(hours=8)))
    db.add(LoginEvent(user_id=user.id))
    await db.commit()
    return {**profile(user), 'token': token}


@router.get('/me')
async def me(user: User = Depends(current_user)):
    return profile(user)


@router.post('/logout')
async def logout(credentials: HTTPAuthorizationCredentials = Depends(security),
                 db: AsyncSession = Depends(get_db_session)):
    if credentials:
        await db.execute(delete(LoginSession).where(LoginSession.token_hash == digest(credentials.credentials)))
        await db.commit()
    return {'ok': True}
