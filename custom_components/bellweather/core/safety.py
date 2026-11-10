"""Defensive rules for everything that comes from the network.

Every byte from a weather service is untrusted. A service can be broken,
hijacked or impersonated, so nothing it sends may:

- use unbounded memory or CPU (size caps, decompression caps, line caps);
- move a request elsewhere (no redirects, https only);
- put strange numbers into the choice (non-finite values, impossible
  coordinates, times in the future);
- put strange text into Home Assistant (control characters, very long
  names);
- leak the location through logs or diagnostics (errors never carry the URL
  or its query).

docs/security.md explains each rule and why it is there.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta

# Largest response accepted per kind of request. Each is far above what the
# services send today (measured 2026-11-09), so the caps cost nothing.
MAX_JSON_BYTES = 2_000_000  # the largest answers (11 models' forecast, 90 days of past runs) are under 400 KB
MAX_TEXT_BYTES = 3_000_000  # the generic text cap; each text below has its own, smaller one
MAX_IMAGE_BYTES = 1_000_000  # a 256 px map tile is about 2-35 KB
MAX_ARCHIVE_BYTES = 4_000_000  # the IMS daily file is about 450 KB
# Per service, measured 2026-11-09 (largest normal answer in brackets):
CAP_ESOH = 4_000_000  # E-SOH area, 100 km box in Germany (164 KB)
CAP_METAR = 2_000_000  # airports in a 100 km box (a few KB)
CAP_SMALL_JSON = 1_000_000  # Open-Meteo current, elevation, MET nowcast, RainViewer index (under 10 KB)
CAP_COLOURS = 64_000  # RainViewer colour table (24 KB)
CAP_INDEX = 300_000  # NSIDC directory page (43 KB)
CAP_ARCHIVE_CSV = 1_500_000  # 65 days of one airport's reports (about 150 KB)
MAX_CSV_FIELDS = 16

# Decompressed sizes.
MAX_PNG_PIXELS = 1024 * 1024
MAX_IMS_TEXT_BYTES = 45_000_000  # the 4 km grid is 6144 x 6145 characters, about 37.8 MB
MAX_LINE_CHARS = 8_192

FUTURE_TOLERANCE = timedelta(minutes=10)

_ID = re.compile(r"^[A-Za-z0-9._:-]{1,40}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
# Format characters (bidi overrides, zero-width joiners ...) can make a name
# look like another one; they never belong in a station name.
_FORMAT = re.compile("[\u00ad\u061c\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff\ufff9-\ufffb]")


class UnsafeResponse(ValueError):
    """A response broke one of the rules above."""


class HttpStatusError(Exception):
    """An HTTP error without the URL in it."""

    def __init__(self, status: int, retry_after: float | None = None) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
        self.retry_after = retry_after


def finite(value: object) -> float | None:
    """A float, or None for anything that is not a finite number."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def valid_position(latitude: float | None, longitude: float | None) -> bool:
    return (
        latitude is not None and longitude is not None
        and -90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0
    )


def clean_text(text: object, max_chars: int = 80) -> str:
    """Printable text of bounded length, for names shown in Home Assistant."""
    return _FORMAT.sub("", _CONTROL.sub("", str(text or ""))).strip()[:max_chars]


def valid_id(text: object) -> str | None:
    """A station id the user typed or a service sent, or None if it looks wrong."""
    value = str(text or "").strip()
    return value if _ID.match(value) else None


def not_future(observed: datetime, now: datetime) -> bool:
    """A time in the future would never age out of the freshness check."""
    return observed <= now + FUTURE_TOLERANCE


def safe_error(error: BaseException) -> str:
    """An error description for logs, attributes and diagnostics: the type
    and the HTTP status only. Library messages often contain the request
    URL, and the URL contains the shared point."""
    status = getattr(error, "status", None)
    name = type(error).__name__
    return f"{name} (HTTP {status})" if isinstance(status, int) else name


def csv_rows(text: str, wanted: list[str], max_rows: int) -> list[dict[str, str]]:
    """Rows of a CSV as dicts with only the wanted columns. The header may
    have at most MAX_CSV_FIELDS fields and 1 KB; csv.DictReader would copy
    every header key into every row, which a crafted file can blow up."""
    import csv
    import io
    import itertools

    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header or len(header) > MAX_CSV_FIELDS or sum(len(h) for h in header) > 1024:
        raise UnsafeResponse("unexpected CSV header")
    index = {name: header.index(name) for name in wanted if name in header}
    rows: list[dict[str, str]] = []
    for row in itertools.islice(reader, max_rows):
        rows.append({name: (row[i][:64] if i < len(row) else "") for name, i in index.items()})
    return rows


def csv_header(text: str) -> list[str]:
    """The parsed first row (handles quoted names), within the same limits."""
    import csv
    import io

    header = next(csv.reader(io.StringIO(text.split("\n", 1)[0][:1024])), None) or []
    if len(header) > MAX_CSV_FIELDS:
        raise UnsafeResponse("unexpected CSV header")
    return header


async def read_capped(chunks, limit: int) -> bytes:
    """Join the chunks of a response, refusing it as soon as it passes the
    limit. Counted on what arrives after decompression."""
    parts: list[bytes] = []
    size = 0
    async for chunk in chunks:
        size += len(chunk)
        if size > limit:
            raise UnsafeResponse("response too large")
        parts.append(chunk)
    return b"".join(parts)


def check_url(url: str) -> None:
    if not url.startswith("https://"):
        raise UnsafeResponse("only https URLs are allowed")
