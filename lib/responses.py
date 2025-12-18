"""Shared response models"""

from pydantic import BaseModel


class SimpleResponse(BaseModel):
    """Simple success response"""

    success: bool
