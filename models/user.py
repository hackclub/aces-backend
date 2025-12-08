"""User database models"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    ARRAY,
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Boolean,
)
from sqlalchemy.orm import declarative_base, relationship, Mapped, MappedColumn

Base = declarative_base()


class User(Base):
    """User table"""

    # TODO: i don't know what we need but i sure as hell know we need more than ts
    # TODO: dm hana about ts question mark? idk.

    __tablename__ = "users"

    id: Mapped[int] = MappedColumn(
        Integer, autoincrement=True, primary_key=True, unique=True
    )
    email: Mapped[str] = MappedColumn(String, nullable=False, unique=True)
    permissions: Mapped[list[int]] = MappedColumn(
        ARRAY(SmallInteger), nullable=False, server_default="{}"
    )
    hackatime_id: Mapped[Optional[int]] = MappedColumn(
        Integer, nullable=True, unique=True, default=None
    )
    username: Mapped[Optional[str]] = MappedColumn(
        String, nullable=True, unique=False, default=None
    )
    projects: Mapped[list["UserProject"]] = relationship(
        "UserProject", back_populates="user", cascade="all, delete-orphan"
    )
    marked_for_deletion: Mapped[bool] = MappedColumn(
        Boolean, nullable=False, default=False
    )
    date_for_deletion: Mapped[Optional[datetime]] = MappedColumn(
        DateTime(timezone=True), nullable=True, default=None
    )
    hackatime_last_fetched: Mapped[datetime] = MappedColumn(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    cards_balance: Mapped[int] = MappedColumn(
        Integer, nullable=False, default=0, 
    )


class UserProject(Base):
    """User Project table"""

    __tablename__ = "projects"

    id: Mapped[int] = MappedColumn(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = MappedColumn(String, nullable=False)
    user_email: Mapped[str] = MappedColumn(
        String, ForeignKey("users.email"), nullable=False
    )
    hackatime_projects: Mapped[list[str]] = MappedColumn(
        JSON, nullable=False, default=list
    )
    hackatime_total_hours: Mapped[float] = MappedColumn(
        Float, nullable=False, default=0.0
    )
    last_updated: Mapped[datetime] = MappedColumn(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    repo: Mapped[str] = MappedColumn(String, nullable=True, default="")
    demo_url: Mapped[str] = MappedColumn(String, nullable=True, default="")
    preview_image: Mapped[str] = MappedColumn(String, nullable=True, default="")
    awarded_cards: Mapped[int] = MappedColumn(Integer, nullable=True, default=0)
    shipped: Mapped[bool] = MappedColumn(Boolean, nullable=False, default=False)

    # Relationship back to user
    user: Mapped["User"] = relationship("User", back_populates="projects")
