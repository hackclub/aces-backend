"""Rate limiting configuration"""

import os

from slowapi import Limiter
from slowapi.util import get_remote_address

REDIS_HOST = os.getenv("REDIS_URL")

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["120/minute"],
    headers_enabled=True,
    storage_uri=REDIS_HOST,
    storage_options={
        "password": os.getenv("REDIS_PASSWORD", ""),
    },
    key_prefix="rt-",
    key_style="endpoint",
    in_memory_fallback_enabled=True,
)
