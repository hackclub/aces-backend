"""User database models"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    ARRAY,
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, MappedColumn, declarative_base, relationship

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
    devlogs: Mapped[list["Devlog"]] = relationship(
        "Devlog", back_populates="user", cascade="all, delete-orphan"
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
        Integer,
        nullable=False,
        default=0,
    )
    referral_code_used: Mapped[Optional[str]] = MappedColumn(
        String(64), nullable=True, default=None
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
    shipped: Mapped[bool] = MappedColumn(Boolean, nullable=False, default=False)
    devlogs: Mapped[list["Devlog"]] = relationship(
        "Devlog", back_populates="project", cascade="all, delete-orphan"
    )

    # Relationship back to user
    user: Mapped["User"] = relationship("User", back_populates="projects")


class Devlog(Base):
    """Devlog posts"""

    __tablename__ = "devlogs"

    id: Mapped[int] = MappedColumn(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = MappedColumn(Integer, ForeignKey("users.id"), nullable=False)
    project_id: Mapped[int] = MappedColumn(
        Integer, ForeignKey("projects.id"), nullable=False
    )
    content: Mapped[str] = MappedColumn(Text, nullable=False)
    media_url: Mapped[str] = MappedColumn(Text, nullable=False)
    created_at: Mapped[datetime] = MappedColumn(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[Optional[datetime]] = MappedColumn(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        onupdate=lambda: datetime.now(timezone.utc),
    )
    hours_snapshot: Mapped[float] = MappedColumn(Float, nullable=False)
    cards_awarded: Mapped[int] = MappedColumn(Integer, nullable=False, default=0)
    state: Mapped[int] = MappedColumn(Integer, nullable=False, default=0)

    # Relationship back to user
    user: Mapped["User"] = relationship("User", back_populates="devlogs")
    project: Mapped["UserProject"] = relationship(
        "UserProject", back_populates="devlogs"
    )
