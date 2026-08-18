from dataclasses import dataclass, field
from typing import List


@dataclass
class DocumentSnapshot:
    """Structured, objective representation of a single SEC filing.

    Contains no classification or interpretation — just what was found in
    the text, ready to be consumed by business rules or an LLM downstream.
    """

    filename: str
    form_type: str
    items: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    money: List[str] = field(default_factory=list)
    percentages: List[str] = field(default_factory=list)
    dates: List[str] = field(default_factory=list)
    companies: List[str] = field(default_factory=list)
    people: List[str] = field(default_factory=list)
    agreements: List[str] = field(default_factory=list)
    sections: List[str] = field(default_factory=list)
