"""
Database utilities and SQLAlchemy session management.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv
load_dotenv()


DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    import logging
    import sys
    # Configure logging if not already configured
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.ERROR,
            format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            stream=sys.stderr
        )
    logger = logging.getLogger(__name__)
    error_msg = (
        "=" * 60 + "\n"
        "FATAL ERROR: DATABASE_URL environment variable is required!\n"
        "=" * 60 + "\n"
        "To fix this:\n"
        "1. Go to Render Dashboard → Your Service → Environment\n"
        "2. Add environment variable: DATABASE_URL\n"
        "3. Set value to your PostgreSQL connection string\n"
        "   Example: postgresql://user:pass@host:5432/dbname?sslmode=require\n"
        "4. Save and redeploy\n"
        "=" * 60
    )
    logger.error(error_msg)
    print(error_msg, file=sys.stderr)
    raise RuntimeError(
        "DATABASE_URL environment variable is required. "
        "Set it in Render dashboard → Environment → DATABASE_URL"
    )

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    # Required for SQLite when used across multiple threads (SocketIO workers).
    connect_args["check_same_thread"] = False

# Add connection timeout for faster failure if DB is unreachable
connect_args_with_timeout = connect_args.copy()
if DATABASE_URL.startswith("postgresql"):
    # PostgreSQL connection timeout (in seconds)
    connect_args_with_timeout.setdefault("connect_timeout", 10)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=3600,  # Recycle connections after 1 hour
    pool_timeout=10,    # Timeout when getting connection from pool
    future=True,
    echo=False,
    connect_args=connect_args_with_timeout,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)

Base = declarative_base()


def init_db() -> None:
    """
    Import models and create tables. Should be invoked once during startup.
    """
    import logging
    try:
        from app import models  # noqa: F401  (side-effect import)
        Base.metadata.create_all(bind=engine)
        logging.info("Database initialized successfully")
    except Exception as e:
        logging.error(f"Error initializing database: {e}", exc_info=True)
        raise


@contextmanager
def db_session() -> Generator:
    """
    Context manager that yields a SQLAlchemy session and guarantees cleanup.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

