"""Devlog review sync job - syncs review decisions from Airtable"""

import asyncio
import logging
import os
from pyairtable import Api
from sqlalchemy import select

from db.main import get_session
from models.main import Devlog

logger = logging.getLogger(__name__)

DEFAULT_CARDS_PER_HOUR = 8


async def sync_devlog_reviews():
    """Sync devlog review decisions and multiplier from Airtable.

    Cards are NOT awarded here -- they are calculated and released
    when the project is shipped (see api/v1/projects ship endpoint).
    """
    table_id = os.getenv("AIRTABLE_REVIEW_TABLE_ID", "")
    api_key = os.getenv("AIRTABLE_REVIEW_KEY", "")
    base_id = os.getenv("AIRTABLE_BASE_ID", "")

    if not all([table_id, api_key, base_id]):
        logger.warning("Missing Airtable review config")
        return
    if api_key.strip() == "":
        logger.error("environment variable AIRTABLE_API_KEY is an empty string!")
        return
    api = Api(api_key)  # type: ignore
    table = api.table(base_id, table_id)  # type: ignore

    try:
        records = await asyncio.to_thread(table.all)
        processed_count = 0

        for record in records:
            fields = record.get("fields", {})
            devlog_id = fields.get("Devlog ID")
            airtable_status = fields.get("Status")

            if not devlog_id or not isinstance(devlog_id, (int, float)):
                continue

            devlog_id = int(devlog_id)

            if not isinstance(airtable_status, str) or airtable_status not in [
                "Pending",
                "Approved",
                "Rejected",
                "Other",
            ]:
                logger.warning(
                    "Unknown status '%s' for devlog %d", airtable_status, devlog_id
                )
                continue

            async with get_session() as session:
                async with session.begin():
                    result = await session.execute(
                        select(Devlog).where(Devlog.id == devlog_id).with_for_update()
                    )
                    devlog = result.scalar_one_or_none()

                    if devlog is None:
                        logger.warning("Devlog ID %d not found in database", devlog_id)
                        continue

                    multiplier = int(fields.get("Multiplier", DEFAULT_CARDS_PER_HOUR))
                    if (
                        devlog.state == airtable_status
                        and devlog.cards_per_hour == multiplier
                    ):
                        continue

                    devlog.state = airtable_status
                    devlog.cards_per_hour = multiplier
                    processed_count += 1

                    logger.info(
                        "Synced devlog %d: state=%s, cards_per_hour=%d",
                        devlog.id,
                        airtable_status,
                        multiplier,
                    )

        logger.info(
            "Devlog review sync complete: processed %d updates", processed_count
        )

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.exception("Error syncing devlog reviews from Airtable: %s", str(e))
