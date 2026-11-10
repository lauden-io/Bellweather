"""What every source looks like."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Protocol

from ..model import Kind, Location, Reading
from ..privacy import Precision


class Http(Protocol):
    """The few HTTP calls the sources need.

    Home Assistant passes its shared aiohttp session behind this; the tests
    and the smoke script pass urllib. HTTP 404 from get_json returns None
    ("nothing for this area", e.g. a box outside a regional service); other errors
    raise. Every implementation follows `core.safety`: https only, no
    redirects, a size cap per response, errors without the URL.
    """

    async def get_json(self, url: str, params: dict[str, str] | None = None, limit: int = ...) -> Any: ...

    async def get_bytes(self, url: str, params: dict[str, str] | None = None, limit: int = ...) -> bytes: ...

    async def get_text(self, url: str, params: dict[str, str] | None = None, limit: int = ...) -> str: ...


class Source(ABC):
    key: str
    kind: Kind
    attribution: str
    # Shown in the docs and the options: what the service receives.
    sends: str

    @abstractmethod
    async def fetch(
        self, http: Http, location: Location, precision: Precision, now: datetime
    ) -> list[Reading]:
        """Readings near the location. Distances are measured inside Home Assistant, from the
        exact location; only what `privacy` allows is sent out."""
