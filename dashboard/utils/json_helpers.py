"""Safe decoding of the *_json columns stored in llm_filing_analysis —
malformed or missing JSON never raises, it just yields an empty list, since
not every filing's analysis populates every optional field.
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
