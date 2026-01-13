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
    table_id = os.getenv("AIRTABLE_REVIEW_TABLE_ID", "")
    api_key = os.getenv("AIRTABLE_REVIEW_KEY", "")
    base_id = os.getenv("AIRTABLE_BASE_ID", "")

    if not all([table_id, api_key, base_id]):
        logger.warning("Missing Airtable review config")
        return
    # Validate api_key is not empty
    if api_key.strip() == "":
        logger.error("environment variable AIRTABLE_API_KEY is an empty string!")
        return
    api = Api(api_key)  # type: ignore
    table = api.table(base_id, table_id)  # type: ignore

    try:
        # Fetch all records from Airtable
        records = await asyncio.to_thread(table.all)

        updates_to_airtable: list[dict[str, Any]] = []
        processed_count = 0

        for record in records:
            fields = record.get("fields", {})
            airtable_record_id = record.get("id")

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
                # Skip if status is not a valid status
                logger.warning(
                    "Unknown status '%s' for devlog %d", airtable_status, devlog_id
                )
                continue

            # Fetch the devlog with lock in a dedicated session/transaction per record
            async with get_session() as session:
                async with session.begin():
                    result = await session.execute(
                        select(Devlog).where(Devlog.id == devlog_id).with_for_update()
                    )
                    devlog = result.scalar_one_or_none()

                    if devlog is None:
                        logger.warning("Devlog ID %d not found in database", devlog_id)
                        continue

                    # Check if status has changed
                    if devlog.state == airtable_status:
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

                    if airtable_status == "Approved":  # ACCEPTED
                        devlog.state = airtable_status

                        # only award cards if transitioning TO accepted to avoid double awarding
                        if old_state != "Approved":
                            prev_result = await session.execute(
                                select(Devlog.hours_snapshot)
                                .where(
                                    Devlog.project_id == devlog.project_id,
                                    Devlog.id < devlog.id,
                                    Devlog.state == "Approved",
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

                    else:
                        if (
                            old_state == "Approved"
                            and devlog.cards_awarded
                            and airtable_status == "Rejected"
                        ):
                            user_result = await session.execute(
                                select(User)
                                .where(User.id == devlog.user_id)
                                .with_for_update()
                            )
                            user = user_result.scalar_one_or_none()
                            if user:
                                user.cards_balance -= devlog.cards_awarded
                                logger.info(
                                    "Rescinded %d cards for devlog %d user %d",
                                    devlog.cards_awarded,
                                    devlog.id,
                                    devlog.user_id,
                                )
                            else:
                                logger.error(
                                    "User %d not found for devlog %d when rescinding",
                                    devlog.user_id,
                                    devlog.id,
                                )
                            devlog.cards_awarded = 0

                        if airtable_status == "Rejected":  # REJECTED
                            devlog.state = airtable_status
                            logger.info("Rejected devlog %d", devlog.id)

                        elif airtable_status == "Other":  # OTHER
                            devlog.state = airtable_status

                        else:  # Pending or unknown
                            devlog.state = airtable_status

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
