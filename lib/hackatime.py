"""Hackatime API stuff"""

import os
from logging import warning
from typing import Dict, List, Optional

import httpx
import validators
from pydantic import BaseModel
from sqlalchemy import text

HACKATIME_ADMIN_API_URL = "https://hackatime.hackclub.com/api/admin/v1"
HACKATIME_API_URL = "https://hackatime.hackclub.com/api/v1"
HACKATIME_API_KEY = os.getenv("HACKATIME_API_KEY", "")
CUTOFF_DATE = "2025-12-01T00:00:00Z"  # example


class UnknownError(Exception):
    """Custom exception for unknown errors"""


class HackatimeAccountResponse(BaseModel):
    """Hackatime account response model"""

    id: int
    username: str


async def get_account(email: str) -> Optional[HackatimeAccountResponse]:
    """Fetch Hackatime account details by email

    Args:
        email (str): User email address.

    Raises:
        ValueError: Invalid email format.
        UnknownError: Hackatime API error.

    Returns:
        Optional[HackatimeAccountResponse]: Hackatime account details or None if not found.
    """

    if not HACKATIME_API_KEY:
        warning("HACKATIME_API_KEY not set, returning mock data")
        return HackatimeAccountResponse(id=1, username="TestUser")

    if not validators.email(email):
        raise ValueError("Invalid email format.")

    sanitized_email = email.replace("'", "''")

    headers = {
        "Authorization": f"Bearer {HACKATIME_API_KEY}",
        "Content-Type": "application/json",
    }

    query = (
        text("""SELECT
    users.id,
    users.username,
    users.github_username,
    users.slack_username,
    email_addresses.email
FROM
    users
    INNER JOIN email_addresses ON users.id = email_addresses.user_id
WHERE
    email_addresses.email = :sanitized_email
LIMIT 1;""")
        .bindparams(sanitized_email=sanitized_email)
        .compile(compile_kwargs={"literal_binds": True})
    )

    body = {
        "query": str(query),
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{HACKATIME_ADMIN_API_URL}/execute", json=body, headers=headers, timeout=10
        )

    if response.status_code != 200:
        raise UnknownError(f"Error fetching account: {response.text}")

    data = response.json()

    if not data.get("rows") or len(data["rows"]) == 0:
        return None

    try:
        account_data = data.get("rows", [])[0]
        username = (
            account_data.get("username")[1]
            or account_data.get("github_username")[1]
            or account_data.get("slack_username")[1]
            or "unknown"
        )
        user_id = account_data.get("id")[1]
    except (IndexError, KeyError, TypeError) as e:
        raise UnknownError(f"Error parsing account data: {e}") from e

    if not user_id:
        raise UnknownError("Account ID not found in response.")

    return HackatimeAccountResponse(
        id=user_id,
        username=username,
    )


async def get_projects(
    user: int, projects_filter: Optional[List[str]] = None
) -> Dict[str, Optional[int]]:
    """Fetch Hackatime project data by project ID

    Args:
        user (int): Hackatime user ID
        projects_filter (Optional[List[str]], optional): List of project names to filter. \
            Defaults to None.

    Raises:
        UnknownError: Hackatime API error.

    Returns:
        Dict (str, Optional[int]): Dictionary mapping project names to total \
            seconds spent (None if not found).
    """

    if projects_filter is not None and len(projects_filter) == 0:
        return {}

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{HACKATIME_API_URL}/users/{user}/stats",
            params={"features": "projects", "start_date": CUTOFF_DATE},
            timeout=10,
        )

    if response.status_code != 200:
        raise UnknownError(f"Error fetching projects: {response.text}")

    data = response.json().get("data", {})

    projects = data.get("projects", [])

    if projects_filter:
        projects = [p for p in projects if p.get("name") in projects_filter]

    hackatime_projects = {
        project.get("name"): project.get("total_seconds", 0) for project in projects
    }

    if projects_filter:
        for project_name in projects_filter:
            if project_name not in hackatime_projects:
                hackatime_projects[project_name] = None

    return hackatime_projects
