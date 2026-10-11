"""Salzburg Verkehr web API client, independent of Home Assistant.

Request parameters were verified against the operator's website and live API.
No personal credentials, HAR files, or ÖBB services are used.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

from .const import REQUEST_LIMIT

BASE_URL = "https://fahrplan.salzburg-verkehr.at"
CONFIG_URL = f"{BASE_URL}/webapp/config/webapp.config.json"
TIMEZONE = ZoneInfo("Europe/Vienna")


class VerkehrError(Exception):
    """A safe error message suitable for Home Assistant logs."""


@dataclass(frozen=True, slots=True)
class Station:
    """An operator stop, identified by the API rather than guessed IDs."""

    id: str
    name: str
    lid: str


@dataclass(frozen=True, slots=True)
class Departure:
    """A scheduled departure with an optional, distinct realtime prediction."""

    scheduled_time: datetime
    realtime_time: datetime | None
    line: str
    product: str
    category: str
    direction: str
    boarding_stop: str
    stop_id: str
    cancelled: bool
    intermediate_stops: tuple[str, ...]

    @property
    def time(self) -> datetime:
        return self.scheduled_time if self.cancelled else (self.realtime_time or self.scheduled_time)

    @property
    def delay_minutes(self) -> float | None:
        if self.realtime_time is None or self.cancelled:
            return None
        return (self.realtime_time.timestamp() - self.scheduled_time.timestamp()) / 60

    @property
    def delay(self) -> bool:
        """Whether an available prediction indicates a positive delay."""
        minutes = self.delay_minutes
        return minutes is not None and minutes > 0


@dataclass(frozen=True, slots=True)
class Board:
    """One response, including choices needed by the configuration UI."""

    departures: tuple[Departure, ...]
    stop_names: tuple[str, ...]
    categories: tuple[str, ...]
    updated_at: datetime
    truncated: bool
    next_departures: tuple[Departure, ...] = ()


def parse_time(date: str, clock: str) -> datetime:
    """Decode HHMMSS or DDHHMMSS; DD is days after the journey date."""
    if not isinstance(clock, str) or len(clock) not in (6, 8) or not clock.isdigit():
        raise ValueError("Invalid departure time")
    days = int(clock[:2]) if len(clock) == 8 else 0
    clock = clock[-6:]
    hours, minutes, seconds = int(clock[:2]), int(clock[2:4]), int(clock[4:])
    if minutes > 59 or seconds > 59:
        raise ValueError("Invalid departure time")
    return (
        datetime.strptime(date, "%Y%m%d") + timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
    ).replace(tzinfo=TIMEZONE)


def _reference(items: list, index: Any) -> Any:
    if type(index) is not int or index < 0:
        raise ValueError("Invalid reference")
    return items[index]


def parse_board(result: dict, now: datetime) -> Board:
    """Fail on changed schemas instead of publishing an invented empty board."""
    try:
        common = result["common"]
        locations, products = common["locL"], common["prodL"]
        journeys = result["jnyL"]
        if not isinstance(journeys, list):
            raise ValueError("Invalid journeys")
        departures = []
        stops, categories = set(), set()
        for journey in journeys:
            stop = journey["stbStop"]
            location = _reference(locations, stop["locX"])
            product = _reference(products, stop.get("dProdX", journey.get("prodX")))
            context = product.get("prodCtx", {})
            category = context.get("catOutL") or "Other"
            name = location["name"]
            stops.add(name)
            categories.add(category)
            scheduled = parse_time(journey["date"], stop["dTimeS"])
            predicted = parse_time(journey["date"], stop["dTimeR"]) if stop.get("dTimeR") else None
            cancelled = bool(journey.get("isCncl") or stop.get("dCncl") or stop.get("isCncl"))
            following = []
            for leg_stop in journey.get("stopL", []):
                # Only later stops in the journey count as an intermediate-stop filter.
                if leg_stop.get("idx", -1) <= stop.get("idx", -1):
                    continue
                following.append(_reference(locations, leg_stop["locX"])["name"])
            departure = Departure(
                scheduled_time=scheduled,
                realtime_time=predicted,
                line=str(context.get("line") or product.get("nameS") or product["name"]),
                product=product["name"],
                category=category,
                direction=journey.get("dirTxt", ""),
                boarding_stop=name,
                stop_id=str(location["extId"]),
                cancelled=cancelled,
                intermediate_stops=tuple(following),
            )
            if departure.time.timestamp() >= now.timestamp():
                departures.append(departure)
        departures.sort(key=lambda d: (d.time.timestamp(), d.line, d.direction))
        return Board(
            tuple(departures), tuple(sorted(stops)), tuple(sorted(categories)), now, len(journeys) >= REQUEST_LIMIT
        )
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as error:
        raise VerkehrError("Unexpected departure data from Salzburg Verkehr") from error


def tokens(value: str) -> tuple[str, ...]:
    return tuple(sorted({part.strip().casefold() for part in value.split(",") if part.strip()}))


def filter_board(
    board: Board,
    *,
    lines: str = "",
    directions: str = "",
    via: str = "",
    categories: list[str] | tuple[str, ...] = (),
    boarding_stop: str = "",
    limit: int = 20,
) -> Board:
    """Apply filters after receiving the complete operator board."""
    line_tokens, direction_tokens, via_tokens = tokens(lines), tokens(directions), tokens(via)
    category_tokens = {category.casefold() for category in categories}
    matching = []
    for departure in board.departures:
        if boarding_stop and departure.boarding_stop != boarding_stop:
            continue
        if line_tokens and not ({departure.line.casefold(), departure.product.casefold()} & set(line_tokens)):
            continue
        if direction_tokens and not any(t in departure.direction.casefold() for t in direction_tokens):
            continue
        if via_tokens and not any(t in stop.casefold() for t in via_tokens for stop in departure.intermediate_stops):
            continue
        if category_tokens and departure.category.casefold() not in category_tokens:
            continue
        matching.append(departure)
    return replace(
        board,
        departures=tuple(matching[:limit]),
        next_departures=tuple(departure for departure in matching if not departure.cancelled)[:2],
    )


class SalzburgClient:
    """Use the shared webapp identifier from public settings, cached for a day."""

    def __init__(self, session: aiohttp.ClientSession):
        self._session = session
        self._auth: dict | None = None
        self._expires = 0.0
        self._lock = asyncio.Lock()

    async def _json(self, url: str, payload: dict | None = None) -> dict:
        try:
            async with self._session.request(
                "POST" if payload is not None else "GET",
                url,
                json=payload,
                headers={"Accept": "application/json", "Origin": BASE_URL},
                timeout=aiohttp.ClientTimeout(total=20),
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise VerkehrError(f"Salzburg Verkehr returned HTTP {response.status}")
                length = response.headers.get("Content-Length", "0")
                if length.isdigit() and int(length) > 4 * 1024 * 1024:
                    raise VerkehrError("Salzburg Verkehr response is too large")
                raw = await response.read()
                if len(raw) > 4 * 1024 * 1024:
                    raise VerkehrError("Salzburg Verkehr response is too large")
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise VerkehrError("Unexpected Salzburg Verkehr response")
                return result
        except (aiohttp.ClientError, TimeoutError) as error:
            raise VerkehrError("Cannot connect to Salzburg Verkehr") from error
        except (ValueError, UnicodeError) as error:
            raise VerkehrError("Invalid JSON from Salzburg Verkehr") from error

    async def _settings(self) -> dict:
        async with self._lock:
            if self._auth is None or time.monotonic() >= self._expires:
                config = await self._json(CONFIG_URL)
                auth = config.get("hciAuth", {})
                if (
                    config.get("urlMgate") != "/hamm/gate"
                    or not isinstance(auth, dict)
                    or not isinstance(auth.get("aid"), str)
                    or not auth["aid"]
                ):
                    raise VerkehrError("Salzburg Verkehr webapp settings changed")
                self._auth = {"type": "AID", "aid": auth["aid"]}
                self._expires = time.monotonic() + 86400
            return self._auth

    async def _request(self, method: str, request: dict) -> dict:
        for attempt in range(2):
            auth = await self._settings()
            payload = {
                "id": str(time.time_ns()),
                "ver": "1.59",
                "lang": "deu",
                "client": {"id": "VAO", "type": "WEB", "name": "webapp", "l": "vs_svv", "v": 10022},
                "auth": auth,
                "formatted": False,
                "ext": "VAO.22",
                "svcReqL": [{"meth": method, "id": "1|15|", "req": request}],
            }
            data = await self._json(f"{BASE_URL}/hamm/gate", payload)
            services = data.get("svcResL")
            if not isinstance(services, list) or len(services) != 1 or not isinstance(services[0], dict):
                raise VerkehrError("Missing service response from Salzburg Verkehr")
            service = services[0]
            error = str(data.get("err", "OK")) if data.get("err", "OK") != "OK" else str(service.get("err", "OK"))
            if error != "OK":
                if attempt == 0 and "AUTH" in error:
                    self._auth = None
                    continue
                raise VerkehrError("Salzburg Verkehr rejected the request")
            if service.get("meth") != method or not isinstance(service.get("res"), dict):
                raise VerkehrError("Unexpected service response from Salzburg Verkehr")
            return service["res"]
        raise VerkehrError("Cannot load Salzburg Verkehr settings")

    async def search_stations(self, query: str) -> list[Station]:
        result = await self._request(
            "LocMatch", {"input": {"field": "S", "loc": {"type": "S", "dist": 1000, "name": query}, "maxLoc": 10}}
        )
        match = result.get("match", result)
        if not isinstance(match, dict):
            raise VerkehrError("Unexpected station search response")
        locations = match.get("locL", [])
        if not isinstance(locations, list):
            raise VerkehrError("Unexpected station search response")
        try:
            return [Station(str(loc["extId"]), loc["name"], loc["lid"]) for loc in locations if loc.get("type") == "S"]
        except (KeyError, TypeError, AttributeError) as error:
            raise VerkehrError("Unexpected station search data") from error

    async def get_board(self, station: Station) -> Board:
        result = await self._request(
            "StationBoard",
            {
                "stbLoc": {"name": station.name, "lid": station.lid},
                "jnyFltrL": [{"type": "PROD", "mode": "INC", "value": 4087}],
                "type": "DEP",
                "sort": "PT",
                "maxJny": REQUEST_LIMIT,
            },
        )
        return parse_board(result, datetime.now(UTC))
