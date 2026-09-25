import csv
import logging
from pathlib import Path
from typing import Dict, List, Optional

from ..database.db import DB_PATH, upsert_otc_securities

logger = logging.getLogger(__name__)

_SOURCE_LABEL = "OTC Markets Stock Screener"


def _to_float(value: str) -> Optional[float]:
    cleaned = value.strip().replace("$", "").replace("%", "").replace(",", "")
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _to_int(value: str) -> Optional[int]:
    cleaned = value.strip().replace(",", "")
    try:
        return int(cleaned) if cleaned else None
    except ValueError:
        return None


def _parse_row(row: Dict[str, str]) -> Dict:
    return {
        "symbol": row.get("Symbol", "").strip().upper(),
        "security_name": row.get("Security Name", "").strip() or None,
        "tier": row.get("Tier", "").strip() or None,
        "price": _to_float(row.get("Price", "")),
        "volume": _to_int(row.get("Vol", "")),
        "sec_type": row.get("Sec Type", "").strip() or None,
        "country": row.get("Country", "").strip() or None,
        "source": _SOURCE_LABEL,
    }


def _read_csv(csv_path: str) -> List[Dict]:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for raw in csv.DictReader(fh):
            parsed = _parse_row(raw)
            if parsed["symbol"]:
                rows.append(parsed)
            else:
                logger.warning(f"Skipping row with empty symbol: {dict(raw)}")
    return rows


def import_otc_screener_csv(
    csv_path: str, db_path: Path = DB_PATH
) -> tuple[int, int]:
    logger.info(f"Reading OTC Screener CSV: {csv_path}")
    rows = _read_csv(csv_path)
    if not rows:
        logger.warning("CSV contained no valid rows.")
        return 0, 0
    inserted, updated = upsert_otc_securities(rows, db_path)
    logger.info(f"OTC Screener import: {inserted} inserted, {updated} updated.")
    return inserted, updated
