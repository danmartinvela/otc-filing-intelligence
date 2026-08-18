"""Small, stateless formatting helpers shared across pages and components."""
from datetime import date, datetime
from typing import Optional


def format_iso_timestamp(raw: Optional[str]) -> str:
    """'2026-08-05T10:15:00+00:00' -> '05/08/2026 10:15 UTC'. Empty string if raw is falsy."""
    if not raw:
        return "—"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    return dt.strftime("%d/%m/%Y %H:%M UTC")


def format_yyyymmdd(raw: Optional[str]) -> str:
    """'20240515' -> '15/05/2024'. Returns the raw value unchanged if it doesn't parse."""
    if not raw:
        return "—"
    try:
        return datetime.strptime(raw, "%Y%m%d").strftime("%d/%m/%Y")
    except ValueError:
        return raw


def compact_number(value: Optional[float]) -> str:
    """1284 -> '1,284'; 12900 -> '12.9K'; 4200000 -> '4.2M'."""
    if value is None:
        return "—"
    value = float(value)
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if abs(value) >= 10_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:,.0f}"


def format_score(value) -> str:
    if value is None:
        return "—"
    return f"{float(value):.0f}"


def yyyymmdd_to_date(raw: Optional[str], fallback: date) -> date:
    """'20240515' -> date(2024, 5, 15). Falls back to `fallback` if raw is missing/invalid."""
    if not raw:
        return fallback
    try:
        return datetime.strptime(raw, "%Y%m%d").date()
    except ValueError:
        return fallback


def date_to_yyyymmdd(value: date) -> str:
    return value.strftime("%Y%m%d")
