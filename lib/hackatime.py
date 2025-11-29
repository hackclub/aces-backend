"""Hackatime API stuf"""

import os
from typing import Optional

import requests
import validators
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

HACKATIME_ADMIN_API_URL = "https://hackatime.hackclub.com/api/admin/v1"
HACKATIME_API_KEY = os.getenv("HACKATIME_API_KEY", "")
CUTOFF_DATE = "2025-11-26:00:00Z" # example


class UnknownError(Exception):
    """Custom exception for unknown errors"""


class HackatimeAccountResponse(BaseModel):
    """Hackatime account response model"""

    id: int
    username: str


def get_account(email: str) -> Optional[HackatimeAccountResponse]:
    """Fetch Hackatime account details by email"""

    if not HACKATIME_API_KEY:
        print("HACKATIME_API_KEY not set, returning mock data")
        return HackatimeAccountResponse(id=1, username="TestUser")

    if not validators.email(email):
        raise ValueError("Invalid email format.")

    sanitized_email = email.replace("'", "''")

    headers = {
        "Authorization": f"Bearer {HACKATIME_API_KEY}",
        "Content-Type": "application/json",
    }

    body = {
        "query": f"""SELECT
            users.id,
            users.username,
            users.github_username,
            users.slack_username,
            email_addresses.email
          FROM
            users
            INNER JOIN email_addresses ON users.id = email_addresses.user_id
          WHERE
            email_addresses.email = '{sanitized_email}'
          LIMIT 1;"""
    }

    response = requests.post(
        f"{HACKATIME_ADMIN_API_URL}/execute", json=body, headers=headers, timeout=10
    )

    if response.status_code != 200:
        raise UnknownError(f"Error fetching account: {response.text}")

    data = response.json()

    if not data.get("rows") or len(data["rows"]) == 0:
        return None

    account_data = data.get("rows", [])[0]
    username = (
        account_data.get("username")[1]
        or account_data.get("github_username")[1]
        or account_data.get("slack_username")[1]
        or "unknown"
    )
    user_id = account_data.get("id")[1]

    if not user_id:
        raise UnknownError("Account ID not found in response.")

    return HackatimeAccountResponse(
        id=user_id,
        username=username,
    )
