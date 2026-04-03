from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker

from app.config import BASE_DIR


DATABASE_URL = f"sqlite:///{BASE_DIR}/fridgechef.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)
Base = declarative_base()


class Household(Base):
    __tablename__ = "households"

    id = Column(Integer, primary_key=True)
    name = Column(String, default="My Kitchen")
    created_at = Column(DateTime, default=datetime.utcnow)
    scans = relationship("Scan", back_populates="household")
    inventory = relationship("InventoryItem", back_populates="household")
    recipes = relationship("Recipe", back_populates="household")


class Scan(Base):
    __tablename__ = "scans"

    id = Column(Integer, primary_key=True)
    household_id = Column(Integer, ForeignKey("households.id"))
    scanned_at = Column(DateTime, default=datetime.utcnow)
    raw_response = Column(Text)
    preferences_used = Column(Text)
    household = relationship("Household", back_populates="scans")
    inventory_items = relationship("InventoryItem", back_populates="scan")
    recipes = relationship("Recipe", back_populates="scan")


class InventoryItem(Base):
    __tablename__ = "inventory_items"

    id = Column(Integer, primary_key=True)
    household_id = Column(Integer, ForeignKey("households.id"))
    scan_id = Column(Integer, ForeignKey("scans.id"), nullable=True)
    name = Column(String, nullable=False)
    quantity = Column(String, default="some")
    category = Column(String, default="other")
    date_added = Column(DateTime, default=datetime.utcnow)
    expiry_date = Column(DateTime, nullable=True)
    days_fresh_estimate = Column(Integer, nullable=True)
    status = Column(String, default="fresh")
    notes = Column(Text, nullable=True)
    household = relationship("Household", back_populates="inventory")
    scan = relationship("Scan", back_populates="inventory_items")


class Recipe(Base):
    __tablename__ = "recipes"

    id = Column(Integer, primary_key=True)
    household_id = Column(Integer, ForeignKey("households.id"))
    scan_id = Column(Integer, ForeignKey("scans.id"), nullable=True)
    title = Column(String)
    markdown_content = Column(Text)
    nutrition_json = Column(Text, nullable=True)
    preferences_used = Column(Text, nullable=True)
    image_url = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    saved = Column(Boolean, default=False)
    household = relationship("Household", back_populates="recipes")
    scan = relationship("Scan", back_populates="recipes")


class Profile(Base):
    __tablename__ = "profiles"

    id = Column(Integer, primary_key=True)
    household_id = Column(
        Integer, ForeignKey("households.id")
    )
    name = Column(String, default="")
    daily_calories = Column(Integer, default=2000)
    daily_protein = Column(Integer, default=150)
    daily_carbs = Column(Integer, default=200)
    daily_fat = Column(Integer, default=65)
    diet_type = Column(String, default="none")
    cooking_for = Column(Integer, default=1)
    skill_level = Column(String, default="beginner")
    updated_at = Column(
        DateTime, default=datetime.utcnow
    )


def init_db():
    """Create all tables and default household if needed."""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        household = db.query(Household).first()
        if not household:
            household = Household(name="My Kitchen")
            db.add(household)
            db.commit()
            print("[FridgeChef] Default household created")

        profile = db.query(Profile).first()
        if not profile:
            profile = Profile(household_id=1)
            db.add(profile)
            db.commit()
            print("[FridgeChef] Default profile created")
    finally:
        db.close()


def get_db():
    """FastAPI dependency - yields db session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
