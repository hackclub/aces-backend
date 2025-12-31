import asyncio
import logging

from .usercleanup import cleanup_deleted_users

logger = logging.getLogger(__name__)

CLEANUP_INTERVAL = 60 * 60 * 24  # 24h


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
