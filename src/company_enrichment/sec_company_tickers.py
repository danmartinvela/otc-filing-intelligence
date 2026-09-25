import logging
from typing import Dict, List, Union

import requests

logger = logging.getLogger(__name__)

SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SOURCE_LABEL = "SEC company_tickers.json"


def normalize_cik(cik: Union[str, int]) -> str:
    return str(int(cik)).zfill(10)


def download_sec_company_tickers(user_agent: str) -> Dict:
    logger.info(f"Downloading {SEC_COMPANY_TICKERS_URL}")
    response = requests.get(
        SEC_COMPANY_TICKERS_URL,
        headers={"User-Agent": user_agent},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def parse_sec_company_tickers(data: Dict) -> List[Dict]:
    companies: List[Dict] = []
    for entry in data.values():
        try:
            companies.append(
                {
                    "cik": normalize_cik(entry["cik_str"]),
                    "ticker": entry["ticker"],
                    "company_name": entry["title"],
                    "source": _SOURCE_LABEL,
                }
            )
        except (KeyError, ValueError) as exc:
            logger.warning(f"Skipping malformed entry {entry!r}: {exc}")
    return companies
