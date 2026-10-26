from abc import ABC, abstractmethod

from ..models import Tender


class Source(ABC):
    """One tender source. Subclasses fetch pages politely and return raw Tenders."""

    name: str

    @abstractmethod
    def fetch(self) -> list[Tender]: ...
