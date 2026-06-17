from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Filing:
    cik: str
    company_name: str
    form_type: str
    date_filed: str
    filename: str
    filing_url: str
    raw_text: Optional[str] = None
    clean_text: Optional[str] = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
