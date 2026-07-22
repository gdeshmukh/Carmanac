"""Engine and session factory.

One engine per process. Import `SessionLocal` and use it as a context manager:

    with SessionLocal() as session:
        ...
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from carmanac.config import settings

engine = create_engine(settings.database_url, future=True)

# expire_on_commit=False so objects stay readable after commit, which keeps
# ingestion and seeding scripts from re-querying just to log what they wrote.
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
