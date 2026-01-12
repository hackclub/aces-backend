"""Devlog review sync job - syncs review decisions from Airtable"""

import asyncio
import logging
import os
from typing import Any

from pyairtable import Api
from sqlalchemy import select

from db.main import get_session
from models.main import Devlog, User

logger = logging.getLogger(__name__)

CARDS_PER_HOUR = 8


async def sync_devlog_reviews():
    """Sync devlog review decisions from Airtable and update cards awarded"""
    table_id = os.getenv("AIRTABLE_REVIEW_TABLE_ID")
    api_key = os.getenv("AIRTABLE_REVIEW_KEY")
    base_id = os.getenv("AIRTABLE_BASE_ID")

    if not all([table_id, api_key, base_id]):
        logger.warning(
            "Missing Airtable review config: table_id=%s, api_key=%s, base_id=%s",
            bool(table_id),
            bool(api_key),
            bool(base_id),
        )
        return
    # Validate api_key is not empty
    if not api_key or api_key.strip() == "":
        logger.error("AIRTABLE_API_KEY is empty or whitespace only")
        return
    api = Api(api_key)  # type: ignore
    table = api.table(base_id, table_id)  # type: ignore

    try:
        # Fetch all records from Airtable
        records = await asyncio.to_thread(table.all)

        updates_to_airtable: list[dict[str, Any]] = []
        processed_count = 0

        async with get_session() as session:
            for record in records:
                fields = record.get("fields", {})
                airtable_record_id = record.get("id")

                devlog_id = fields.get("Devlog ID")
                airtable_status = fields.get("Status")

                if not devlog_id or not isinstance(devlog_id, (int, float)):
                    continue

                devlog_id = int(devlog_id)

                # Airtable uses numeric status values: 0=Published, 1=Accepted, 2=Rejected, 3=Other
                if not isinstance(airtable_status, (int, float)):
                    # Skip if status is not a number
                    continue

                status_value = int(airtable_status)

                # Fetch the devlog with lock
                async with session.begin():
                    result = await session.execute(
                        select(Devlog).where(Devlog.id == devlog_id).with_for_update()
                    )
                    devlog = result.scalar_one_or_none()

                    if devlog is None:
                        logger.warning("Devlog ID %d not found in database", devlog_id)
                        continue

                    # Check if status has changed
                    if devlog.state == status_value:
                        # Already processed, but ensure Airtable has the cards awarded
                        if devlog.cards_awarded != fields.get("Cards Awarded"):
                            updates_to_airtable.append(
                                {
                                    "id": airtable_record_id,
                                    "fields": {
                                        "Cards Awarded": devlog.cards_awarded,
                                    },
                                }
                            )
                        continue

                    old_state = devlog.state

                    if status_value == 1:  # ACCEPTED
                        devlog.state = status_value

                        # only award cards if transitioning TO accepted to avoid double awarding
                        if old_state != 1:
                            prev_result = await session.execute(
                                select(Devlog.hours_snapshot)
                                .where(
                                    Devlog.project_id == devlog.project_id,
                                    Devlog.id < devlog.id,
                                )
                                .order_by(Devlog.id.desc())
                                .limit(1)
                            )
                            prev_hours = prev_result.scalar() or 0
                            cards = round(
                                (devlog.hours_snapshot - prev_hours) * CARDS_PER_HOUR
                            )
                            devlog.cards_awarded = cards

                            user_result = await session.execute(
                                select(User)
                                .where(User.id == devlog.user_id)
                                .with_for_update()
                            )
                            user = user_result.scalar_one_or_none()
                            if user:
                                user.cards_balance += cards
                            else:
                                logger.error(
                                    "User %d not found for devlog %d",
                                    devlog.user_id,
                                    devlog.id,
                                )
                                continue

                            logger.info(
                                "Accepted devlog %d, awarded %d cards to user %d",
                                devlog.id,
                                cards,
                                devlog.user_id,
                            )

                    elif status_value == 2:  # REJECTED
                        devlog.state = status_value
                        logger.info("Rejected devlog %d", devlog.id)

                    elif status_value == 3:  # OTHER
                        devlog.state = status_value

                    else:  # 0 = PUBLISHED or unknown
                        devlog.state = status_value

                    # queue the update
                    if airtable_record_id:
                        updates_to_airtable.append(
                            {
                                "id": airtable_record_id,
                                "fields": {
                                    "Cards Awarded": devlog.cards_awarded,
                                },
                            }
                        )

                    processed_count += 1

        # batch the update to airtable with cards awarded
        if updates_to_airtable:
            logger.info("Updating %d records in Airtable", len(updates_to_airtable))
            await asyncio.to_thread(table.batch_update, updates_to_airtable)  # type: ignore

        logger.info(
            "Devlog review sync complete: processed %d status changes, updated %d Airtable records",
            processed_count,
            len(updates_to_airtable),
        )

    except Exception:
        logger.exception("Error syncing devlog reviews from Airtable")
