"""Rate limiting configuration"""

import os

from slowapi import Limiter
from slowapi.util import get_remote_address

REDIS_HOST = "redis" if os.getenv("USING_DOCKER") == "true" else "localhost"

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["120/minute"],
    headers_enabled=True,
    storage_uri=f"redis://{REDIS_HOST}:6379/0",
    storage_options={
        "password": os.getenv("REDIS_PASSWORD", ""),
    },
    key_prefix="rt-",
    key_style="endpoint",
)
