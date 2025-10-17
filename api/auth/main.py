from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.exceptions import HTTPException
import os
import secrets
from email.message import EmailMessage
from pydantic import BaseModel
import asyncio
import redis.asyncio as redis
import aiosmtplib
import dotenv
import jwt
from datetime import datetime, timezone, timedelta

dotenv.load_dotenv()

r = redis.Redis()

class OtpClientRequest(BaseModel):
    email: str

class OtpClientResponse(BaseModel):
    email: str
    otp: int

router = APIRouter()

# @router.route("/callback")
# async def callback(request: Request):
#     try:
#         await client.handleSignInCallback(str(request.url)) # Handle a lot of stuff
#         return RedirectResponse("/") # Redirect the user to the home page after a successful sign-in
#     except Exception as e:
#         # Change this to your error handling logic
#         raise HTTPException(500)

# @router.route("/sign-in")
# async def sign_in(request: Request):
#     # Get the sign-in URL and redirect the user to it
#     return RedirectResponse(await client.signIn(
#         redirectUri="http://localhost:8000/callback",
#     ))
    
# @router.route("/sign-up")
# async def sign_up(request: Request):
#     # Get the sign-in URL and redirect the user to it
#     return RedirectResponse(await client.signIn(
#         redirectUri="http://localhost:8000/callback",
#         interactionMode="signUp", # Show the sign-up page on the first screen
#     ))

# @router.route("/sign-out")
# async def sign_out(request: Request):
#     return RedirectResponse(
#         # Redirect the user to the home page after a successful sign-out
#         await client.signOut(postLogoutRedirectUri="http://localhost:8000/")
#     )

#@decorator
def require_auth(func):
    async def wrapper(*args, request: Request, **kwargs):
        if not await is_user_authenticated(request):
            return RedirectResponse("/login", status_code=401)
        return await func(request, *args, **kwargs)
    return wrapper

#@decorator
def require_admin(func):
    async def wrapper(*args, request: Request, **kwargs):
        if not await is_user_authenticated(request) or not await is_user_admin():
            return RedirectResponse("/login", status_code=418)
        elif await is_user_authenticated(request) and not await is_user_admin():
            return RedirectResponse("/home", status_code=403)
        return await func(request, *args, **kwargs)
    return wrapper

#@decorator
def require_reviewer(func):
    async def wrapper(*args, request: Request, **kwargs):
        if not await is_user_authenticated(request) or not await is_user_reviewer():
            return RedirectResponse("/login", status_code=418)
        elif await is_user_authenticated(request) and not await is_user_reviewer():
            return RedirectResponse("/home", status_code=403)
        return await func(request, *args, **kwargs)
    return wrapper

async def is_user_admin() -> bool: ...

async def is_user_reviewer() -> bool: ...

async def is_user_authenticated(request: Request) -> None:
    token = request.cookies.get("sessionId")
    if token is None:
        raise HTTPException(status_code=401)
    try:
        if not os.getenv("JWT_SECRET"):
            raise HTTPException(500)
        decoded = jwt.decode(token, os.getenv("JWT_SECRET", ""), ["HS256"])
        if datetime.now(timezone.utc) - timedelta(days=7) > datetime.fromtimestamp(decoded["iat"], timezone.utc):
            raise HTTPException(status_code=401)
    # TODO: add email verification implementation once postgres is set up
    except Exception:
        raise HTTPException(status_code=401)
     # validate_token(), check cookies, not Authorization: Bearer xyz
    # return True

async def refresh_token(): ...

@router.post("/api/send_otp")
async def send_otp(request: Request, otp_request: OtpClientRequest): 
    otp = secrets.SystemRandom().randrange(100000, 999999)
    await r.setex(f"otp-{otp_request.email}", 300, otp)
    message = EmailMessage()
    message["From"] = os.getenv("SMTP_EMAIL", "example@example.com")
    message["To"] = otp_request.email
    message["Subject"] = "Aces OTP code"
    message.set_content(f"Your OTP for Aces is {otp}! This code will expire in 5 minutes. \n Happy Hacking!\nAces Organizing Team")

    await aiosmtplib.send(
        message, 
        hostname=os.getenv("SMTP_SERVER", "smtp.example.com"), 
        port=465,
        username=message["From"],
        password=os.getenv("SMTP_PWD", ""),
        use_tls=True)
    return {"success": True}

@router.post("/api/validate_otp")
async def validate_otp(request: Request, otp_client_response: OtpClientResponse, response: Response): 
    if not os.getenv("JWT_SECRET"):
        raise HTTPException(status_code=500)
    stored_otp = await r.get(f"otp-{otp_client_response.email}")
    if not stored_otp:
        raise HTTPException(status_code=401, detail="Invalid OTP")
    print(stored_otp)
    if not str(stored_otp.decode("utf-8")).isnumeric(): 
        raise HTTPException(status_code=500)
    if not int(stored_otp) == otp_client_response.otp:
        raise HTTPException(status_code=401, detail="Invalid OTP")
    
    await r.delete(f"otp-{otp_client_response.email}")
    token = await generate_session_id(otp_client_response.email)
    response.set_cookie(key="sessionId", 
                        value=token, 
                        httponly=True, 
                        secure=True,
                        max_age=604800)
    return {"success": True}

async def generate_session_id(email: str) -> str: 
    token = jwt.encode({"sub": email, "iat": int(datetime.now(timezone.utc).timestamp())}, os.getenv("JWT_SECRET", ""), algorithm="HS256")
    return token