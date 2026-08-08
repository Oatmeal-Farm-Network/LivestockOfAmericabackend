# --- routers/forgot_password.py ---
# NOTE: The canonical forgot-password logic lives in routers/auth.py (POST /auth/forgot-password).
# This router is also registered in main.py; FastAPI uses the first-registered route, so this
# file is effectively shadowed. It is kept here as a fallback and kept in sync.

import os
import logging
import sendgrid
from sendgrid.helpers.mail import Mail
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr

from database import get_db_cursor
from auth import hash_password, create_password_reset_token

logger = logging.getLogger(__name__)

router = APIRouter()

SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
FROM_EMAIL       = os.getenv("FROM_EMAIL", "john@oatmeal-ai.com")
SITE_NAME        = os.getenv("SITE_NAME", "Livestock Of America")
FRONTEND_URL     = os.getenv("FRONTEND_URL", "https://livestockofamerica.com")


class ForgotPasswordRequest(BaseModel):
    Email: EmailStr


@router.post("/auth/forgot-password")
async def forgot_password(body: ForgotPasswordRequest):
    email = body.Email.strip().lower()

    # ── 1. Look up user ──────────────────────────────────────────────────────
    try:
        cursor = get_db_cursor()
        cursor.execute(
            "SELECT PeopleID, PeopleFirstName FROM People WHERE LOWER(PeopleEmail) = %s",
            (email,)
        )
        row = cursor.fetchone()
    except Exception as exc:
        logger.exception("MSSQL query failed: %s", exc)
        raise HTTPException(status_code=500, detail="Database error. Please try again.")

    # Always return the same message to avoid user enumeration
    if not row:
        return {"message": "If that email is registered you will receive a reset link.", "email": email}

    people_id  = row.get("PeopleID")
    first_name = row.get("PeopleFirstName", "")

    # ── 2. Generate reset token ──────────────────────────────────────────────
    reset_token = create_password_reset_token(people_id)
    reset_link  = f"{FRONTEND_URL}/reset-password?token={reset_token}"

    # ── 3. Send via SendGrid ─────────────────────────────────────────────────
    if not SENDGRID_API_KEY:
        raise HTTPException(status_code=503, detail="Email service not configured.")

    html_body = f"""
    <font face="arial">
    Dear {first_name},<br><br>
    We received a request to reset your {SITE_NAME} password.
    Click the link below to choose a new password. This link expires in 1 hour.<br><br>
    <a href="{reset_link}">Reset my password</a><br><br>
    If you did not request this, you can safely ignore this email.<br><br>
    Sincerely,<br><br>
    {SITE_NAME}
    </font>
    """

    try:
        sg = sendgrid.SendGridAPIClient(api_key=SENDGRID_API_KEY)
        sg.send(Mail(
            from_email=FROM_EMAIL,
            to_emails=email,
            subject=f"Reset your {SITE_NAME} password",
            html_content=html_body,
        ))
        logger.info("Password reset email sent to %s", email)
    except Exception as exc:
        logger.exception("SendGrid send failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to send email. Please try again.")

    return {"message": "If that email is registered you will receive a reset link.", "email": email}
