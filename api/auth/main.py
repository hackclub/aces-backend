from fastapi import APIRouter
from fastapi.responses import RedirectResponse
from fastapi.exceptions import HTTPException

router = APIRouter()

#@decorator
def require_auth(func):
    async def wrapper(*args, **kwargs):
        if not is_user_authenticated():
            return RedirectResponse("/login", status_code=401)
        return await func(*args, **kwargs)
    return wrapper

#@decorator
def require_admin(func):
    async def wrapper(*args, **kwargs):
        if not is_user_authenticated() or not is_user_admin():
            return RedirectResponse("/login", status_code=401)
        elif is_user_authenticated and not is_user_admin():
            return RedirectResponse("/home", status_code=403)
        return await func(*args, **kwargs)
    return wrapper

#@decorator
def require_reviewer(func):
    async def wrapper(*args, **kwargs):
        if not is_user_authenticated() or not is_user_reviewer():
            return RedirectResponse("/login", status_code=401)
        elif is_user_authenticated and not is_user_reviewer():
            return RedirectResponse("/home", status_code=403)
        return await func(*args, **kwargs)
    return wrapper

async def is_user_admin() -> bool: ...

async def is_user_reviewer() -> bool: ...

async def is_user_authenticated() -> bool: ... # validate_token()

async def refresh_token(): ...

async def send_otp(): ...

async def validate_otp(): ...
