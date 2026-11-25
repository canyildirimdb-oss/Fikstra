import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models import ScraperCache


def get_cached_json(db: Session, cache_key: str, ttl_hours: float) -> Optional[Any]:
    """Return cached JSON payload if present and fresher than ttl_hours."""
    row = db.query(ScraperCache).filter(ScraperCache.cache_key == cache_key).first()
    if not row or not row.updated_at:
        return None
    updated = row.updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - updated > timedelta(hours=ttl_hours):
        return None
    try:
        return json.loads(row.payload_json)
    except Exception:
        return None


def set_cached_json(db: Session, cache_key: str, payload: Any) -> None:
    """Upsert JSON payload into ScraperCache."""
    raw = json.dumps(payload, ensure_ascii=False)
    now = datetime.now(timezone.utc)
    row = db.query(ScraperCache).filter(ScraperCache.cache_key == cache_key).first()
    if row:
        row.payload_json = raw
        row.updated_at = now
    else:
        db.add(ScraperCache(cache_key=cache_key, payload_json=raw, updated_at=now))
    db.flush()
