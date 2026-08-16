from __future__ import annotations

"""
Explicit Stuttgart address search using OpenStreetMap Nominatim.

Important:
- This is NOT autocomplete.
- Call search() only after an explicit user action (for example Enter/Search).
- Results are cached locally.
- Requests are throttled to at most one per second for the public service.
- The service URL is configurable so the project can move to another provider
  or a self-hosted Nominatim instance later.
"""

from dataclasses import dataclass, asdict
import json
from pathlib import Path
import threading
import time
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_SERVICE_URL = (
    "https://nominatim.openstreetmap.org/search"
)

# Broad Stuttgart-area bounding box:
# west, north, east, south when sent as Nominatim's viewbox parameter.
STUTTGART_VIEWBOX = (
    9.00,
    48.90,
    9.35,
    48.65,
)


@dataclass(frozen=True)
class AddressSearchResult:
    display_name: str
    latitude: float
    longitude: float
    osm_type: str | None
    osm_id: int | None
    place_id: int | None
    result_type: str | None
    importance: float | None


class NominatimAddressSearch:
    def __init__(
        self,
        *,
        cache_path: str | Path,
        user_agent: str = (
            "MissionTwin-Stuttgart-Research/1.0"
        ),
        service_url: str = DEFAULT_SERVICE_URL,
        minimum_request_interval_s: float = 1.05,
        timeout_s: float = 12.0,
    ) -> None:
        self.cache_path = Path(
            cache_path
        )
        self.user_agent = str(
            user_agent
        ).strip()
        self.service_url = str(
            service_url
        ).strip()
        self.minimum_request_interval_s = max(
            1.0,
            float(minimum_request_interval_s),
        )
        self.timeout_s = float(
            timeout_s
        )

        if not self.user_agent:
            raise ValueError(
                "A non-empty application User-Agent is required."
            )

        self._lock = threading.Lock()
        self._last_request_monotonic = 0.0
        self._cache = self._load_cache()

    def _load_cache(
        self,
    ) -> dict[str, list[dict[str, Any]]]:
        if not self.cache_path.is_file():
            return {}

        try:
            with self.cache_path.open(
                "r",
                encoding="utf-8",
            ) as handle:
                data = json.load(
                    handle
                )

            if isinstance(data, dict):
                return data
        except (
            OSError,
            json.JSONDecodeError,
        ):
            pass

        return {}

    def _save_cache(
        self,
    ) -> None:
        self.cache_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        temporary = self.cache_path.with_suffix(
            self.cache_path.suffix
            + ".tmp"
        )

        with temporary.open(
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(
                self._cache,
                handle,
                indent=2,
                ensure_ascii=False,
            )

        temporary.replace(
            self.cache_path
        )

    @staticmethod
    def _cache_key(
        query: str,
        limit: int,
    ) -> str:
        return (
            query.strip().casefold()
            + f"|limit={limit}"
        )

    @staticmethod
    def _decode_results(
        raw_results: list[dict[str, Any]],
    ) -> list[AddressSearchResult]:
        decoded = []

        for item in raw_results:
            try:
                latitude = float(
                    item["lat"]
                )
                longitude = float(
                    item["lon"]
                )
            except (
                KeyError,
                TypeError,
                ValueError,
            ):
                continue

            def optional_int(
                key: str,
            ) -> int | None:
                raw = item.get(
                    key
                )

                try:
                    return (
                        None
                        if raw is None
                        else int(raw)
                    )
                except (
                    TypeError,
                    ValueError,
                ):
                    return None

            def optional_float(
                key: str,
            ) -> float | None:
                raw = item.get(
                    key
                )

                try:
                    return (
                        None
                        if raw is None
                        else float(raw)
                    )
                except (
                    TypeError,
                    ValueError,
                ):
                    return None

            decoded.append(
                AddressSearchResult(
                    display_name=str(
                        item.get(
                            "display_name",
                            "",
                        )
                    ),
                    latitude=latitude,
                    longitude=longitude,
                    osm_type=(
                        None
                        if item.get("osm_type") is None
                        else str(item.get("osm_type"))
                    ),
                    osm_id=optional_int(
                        "osm_id"
                    ),
                    place_id=optional_int(
                        "place_id"
                    ),
                    result_type=(
                        None
                        if item.get("type") is None
                        else str(item.get("type"))
                    ),
                    importance=optional_float(
                        "importance"
                    ),
                )
            )

        return decoded

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
    ) -> list[AddressSearchResult]:
        """
        Execute one explicit Stuttgart address/place search.
        """
        query = str(
            query
        ).strip()

        if not query:
            return []

        limit = max(
            1,
            min(
                10,
                int(limit),
            ),
        )

        key = self._cache_key(
            query,
            limit,
        )

        cached = self._cache.get(
            key
        )

        if isinstance(
            cached,
            list,
        ):
            return self._decode_results(
                cached
            )

        with self._lock:
            now = time.monotonic()

            remaining = (
                self.minimum_request_interval_s
                - (
                    now
                    - self._last_request_monotonic
                )
            )

            if remaining > 0.0:
                time.sleep(
                    remaining
                )

            west, north, east, south = STUTTGART_VIEWBOX

            params = {
                "q": query,
                "format": "jsonv2",
                "addressdetails": 1,
                "limit": limit,
                "countrycodes": "de",
                "viewbox": (
                    f"{west},{north},"
                    f"{east},{south}"
                ),
                "bounded": 1,
                "accept-language": "de,en",
            }

            url = (
                self.service_url
                + "?"
                + urlencode(params)
            )

            request = Request(
                url,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "application/json",
                },
                method="GET",
            )

            try:
                with urlopen(
                    request,
                    timeout=self.timeout_s,
                ) as response:
                    payload = json.loads(
                        response.read().decode(
                            "utf-8"
                        )
                    )
            finally:
                self._last_request_monotonic = (
                    time.monotonic()
                )

        if not isinstance(
            payload,
            list,
        ):
            raise RuntimeError(
                "Unexpected Nominatim response."
            )

        self._cache[key] = payload
        self._save_cache()

        return self._decode_results(
            payload
        )


def default_stuttgart_searcher(
    project_root: str | Path,
) -> NominatimAddressSearch:
    project_root = Path(
        project_root
    )

    return NominatimAddressSearch(
        cache_path=(
            project_root
            / "cache"
            / "geocoding"
            / "stuttgart_nominatim.json"
        )
    )
