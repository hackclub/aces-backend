import asyncio
import logging

from .usercleanup import cleanup_deleted_users
from .pyramidsync import sync_users_to_airtable

logger = logging.getLogger(__name__)

CLEANUP_INTERVAL = 60 * 60 * 24  # 24h
PYRAMID_SYNC_INTERVAL = 60 * 10  # 10m


async def run_cleanup():
    """Background loop that runs cleanup every 24h"""

    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL)
            await cleanup_deleted_users()
        except asyncio.CancelledError:
            break
        except Exception as e:
            # Don't crash the whole process, but do surface the error.
            logger.exception("run_cleanup: cleanup_deleted_users failed: %s", e)


async def run_pyramid_sync():
    """Background loop that syncs to Pyramid Scheme Airtable every 10 minutes"""
    # Run immediately on startup
    try:
        await sync_users_to_airtable()
    except Exception as e:
        logger.exception("Initial pyramid sync failed: %s", e)

    while True:
        try:
            await asyncio.sleep(PYRAMID_SYNC_INTERVAL)
            await sync_users_to_airtable()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception("run_pyramid_sync failed: %s", e)
