from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    TR_TZ = ZoneInfo("Europe/Istanbul")
except ZoneInfoNotFoundError:
    TR_TZ = timezone(timedelta(hours=3))


def tr_now() -> datetime:
    return datetime.now(TR_TZ)


def tr_today_str() -> str:
    return tr_now().strftime("%Y-%m-%d")


def to_tr_datetime(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TR_TZ)


def is_tr_today_or_next_24h(start_time_utc: datetime) -> bool:
    now_utc = datetime.now(timezone.utc)
    if start_time_utc.tzinfo is None:
        start_time_utc = start_time_utc.replace(tzinfo=timezone.utc)
    return now_utc - timedelta(minutes=15) <= start_time_utc <= now_utc + timedelta(hours=24)


def is_tr_same_day(start_time_utc: datetime, target_date_str: str | None = None) -> bool:
    target = target_date_str or tr_today_str()
    return to_tr_datetime(start_time_utc).strftime("%Y-%m-%d") == target
