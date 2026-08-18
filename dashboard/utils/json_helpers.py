"""Safe decoding of the *_json columns stored in filing_snapshots and
llm_filing_analysis — malformed or missing JSON never raises, it just yields
an empty list, since a Document Intelligence field being absent is normal
(not every snapshot found every kind of entity).
"""
import json
from typing import List, Optional


def safe_json_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []
