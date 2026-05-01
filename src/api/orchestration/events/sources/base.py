"""EventSource ABC + RawEvent dataclass — cf. spec §1."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Region = Literal["US", "EU", "GB"]
Impact = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class RawEvent:
    """Event tel que renvoyé par une Source, avant dédup et persist.

    Invariant : ``scheduled_at`` est tz-aware UTC (ou convertible). Toute source
    qui produit un naive datetime est buggée — le scheduler le détecte au
    parsing si besoin.
    """

    event_type: str          # "FOMC", "NFP", "CPI", ...
    region: Region
    impact: Impact
    scheduled_at: datetime
    description: str
    source_name: str         # for debugging / cross-source dedup audit


class EventSource(ABC):
    """Contrat pour toute source d'events économiques.

    Subclass devrait :
    - définir ``name`` (str unique)
    - implémenter ``fetch()`` (I/O réseau)
    - garder le parsing dans une méthode ``_parse(payload)`` testable hors réseau
    """

    name: str = "abstract"
    timeout_seconds: float = 10.0
    expected_min_events: int = 1

    @abstractmethod
    async def fetch(self) -> list[RawEvent]:
        """Récupère + parse + filtre. Doit lever une exception en cas d'échec.

        Le scheduler l'attrape — ne pas swallow ici sinon on perd la traçabilité
        des drifts (parser cassé, source 404, etc.).
        """
        ...
