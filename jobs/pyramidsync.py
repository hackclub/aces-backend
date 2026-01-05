import asyncio
import logging
import os

import httpx
from pyairtable import Api
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db.main import get_session
from models.main import User

logger = logging.getLogger(__name__)


async def sync_users_to_airtable():
    """Push user data to Pyramid Scheme Airtable table"""
    table_id = os.getenv("AIRTABLE_PYRAMID_TABLE_ID")
    api_key = os.getenv("AIRTABLE_PYRAMID_API_KEY")
    base_id = os.getenv("AIRTABLE_BASE_ID")
    if not all([table_id, api_key, base_id]):
        logger.warning(
            "Missing Airtable config: table_id=%s, api_key=%s, base_id=%s",
            bool(table_id),
            bool(api_key),
            bool(base_id),
        )
        return

    api = Api(api_key)
    table = api.table(base_id, table_id)

    # Fetch all users with their projects
    async with get_session() as session:
        users = (
            (await session.execute(select(User).options(selectinload(User.projects))))
            .scalars()
            .all()
        )

        records = []
        async with httpx.AsyncClient(timeout=10) as client:
            for user in users:
                idv = "error"
                try:
                    resp = await client.get(
                        "https://auth.hackclub.com/api/external/check",
                        params={"email": user.email},
                    )
                    if resp.status_code == 200:
                        idv = resp.json().get("result", "error")
                except Exception:
                    logger.warning("IDV check failed for %s", user.email, exc_info=True)

                records.append(
                    {
                        "fields": {
                            "Email": user.email,
                            "Hours": round(
                                sum(p.hackatime_total_hours for p in user.projects), 2
                            ),
                            "Projects Shipped": sum(
                                1 for p in user.projects if p.shipped
                            ),
                            "IDV Status": idv,
                            "Referral Code": user.referral_code_used or "",
                        }
                    }
                )

        logger.info("Syncing %d users to Airtable", len(records))
        if not records:
            logger.warning("No records to sync")
            return

        try:
            await asyncio.to_thread(table.batch_upsert, records, key_fields=["Email"])
            logger.info("Airtable sync complete")
        except Exception:
            logger.exception("Airtable batch sync failed")
