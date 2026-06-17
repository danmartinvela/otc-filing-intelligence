import logging
from typing import List, Optional

from ..database.models import Filing

logger = logging.getLogger(__name__)

SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives"


def parse_line(line: str) -> Optional[Filing]:
    """Parse a single pipe-delimited data line from master.idx."""
    parts = line.strip().split("|")
    if len(parts) != 5:
        return None
    cik, company_name, form_type, date_filed, filename = (p.strip() for p in parts)
    # CIK must be numeric; this also skips the header row "CIK|Company Name|..."
    if not cik.isdigit():
        return None
    return Filing(
        cik=cik,
        company_name=company_name,
        form_type=form_type,
        date_filed=date_filed,
        filename=filename,
        filing_url=f"{SEC_ARCHIVES_BASE}/{filename}",
    )


def parse_master_idx(content: str) -> List[Filing]:
    """Parse the full text of a master.idx file into Filing objects."""
    filings: List[Filing] = []
    in_data_section = False

    for line in content.splitlines():
        # The data section starts after the "CIK|..." header line
        if line.startswith("CIK|"):
            in_data_section = True
            continue
        if not in_data_section:
            continue
        # Skip the dashes separator that follows the header
        if line.startswith("-"):
            continue

        filing = parse_line(line)
        if filing:
            filings.append(filing)

    return filings
