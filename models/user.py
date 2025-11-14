from sqlalchemy import (
    Column,
    Integer,
    Float,
    String,
    JSON,
    DateTime,
    ForeignKey
)
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime, timezone
Base = declarative_base()

class User(Base):
    __tablename__ = "user_info"

    email = Column(String, primary_key=True)
    projects = relationship("UserProject", back_populates="user", cascade="all, delete-orphan")



class UserProject(Base):
    __tablename__ = "user_projects"
    
    id = Column(Integer, primary_key=True, autoincrement=True) 
    name = Column(String, nullable=False)
    user_email = Column(String, ForeignKey("user_info.email"), nullable=False) 
    hackatime_projects = Column(JSON, nullable=False, default=list) 
    hackatime_total_hours = Column(Float, nullable=False, default=0.0)
    last_updated = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    
    # Relationship back to user
    user = relationship("User", back_populates="projects")

# class UserProject(Base):
#     id = Column(String, primary_key=True)
    