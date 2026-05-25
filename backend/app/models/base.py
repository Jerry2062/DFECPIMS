"""
models/base.py

Defines the SQLAlchemy declarative base shared by all models.
Keeping Base isolated here avoids circular imports when models
reference each other via relationships.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """
    Central declarative base class.

    All ORM models in DFECPIMS inherit from this. SQLAlchemy uses it
    to track table metadata, which Alembic then inspects for migrations.
    """
    pass