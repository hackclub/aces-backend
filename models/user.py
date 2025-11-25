"""User database models"""

from datetime import datetime, timezone

from sqlalchemy import (
    ARRAY,
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
)
from sqlalchemy.orm import declarative_base, relationship, Mapped, MappedColumn

Base = declarative_base()


class User(Base):
    """User table"""

    __tablename__ = "users"

    id: Mapped[int] = MappedColumn(Integer, autoincrement=True, primary_key=True, unique=True)
    email: Mapped[str] = MappedColumn(String, nullable=False, unique=True)
    permissions: Mapped[list[int]] = MappedColumn(ARRAY(SmallInteger), nullable=False, server_default="{}")
    projects: Mapped[list["UserProject"]] = relationship(
        "UserProject", back_populates="user", cascade="all, delete-orphan"
    )


class UserProject(Base):
    """User Project table"""

    __tablename__ = "projects"

    id: Mapped[int] = MappedColumn(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = MappedColumn(String, nullable=False)
    user_email: Mapped[str] = MappedColumn(String, ForeignKey("users.email"), nullable=False)
    hackatime_projects: Mapped[list[str]] = MappedColumn(JSON, nullable=False, default=list)
    hackatime_total_hours: Mapped[float] = MappedColumn(Float, nullable=False, default=0.0)
    last_updated: Mapped[DateTime] = MappedColumn(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationship back to user
    user: Mapped["User"] = relationship("User", back_populates="projects")

