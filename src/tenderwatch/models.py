from dataclasses import dataclass, field
from datetime import date


@dataclass
class Tender:
    source: str
    url: str
    title: str
    raw_text: str = ""
    buyer: str | None = None
    deadline: date | None = None
    budget_etb: float | None = None
    region: str | None = None
    sector: str | None = None
    requirements: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.url}"


@dataclass
class Company:
    name: str
    contact: str
    sector: str
    keywords: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    min_budget_etb: float = 0
    exclude: list[str] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    size: str | None = None  # e.g. "small, 25 staff" or "Grade 3 contractor"
