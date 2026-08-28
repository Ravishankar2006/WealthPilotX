"""Database engine and session lifecycle.

Sessions are synchronous by choice. FastAPI runs plain `def` endpoints in a thread
pool, which handles this workload comfortably, and sync SQLAlchemy keeps the test
story and the stack traces markedly simpler for a single maintainer.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_settings = get_settings()

engine = create_engine(
    _settings.sqlalchemy_url,
    pool_pre_ping=True,
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a session that always closes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
