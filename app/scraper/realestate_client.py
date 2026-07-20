"""
HTTP client for the GTA5RP wiki `/realestate` catalog.

Unlike the map scraper (which drives a browser and clicks markers), this source
is a Next.js Server Action that returns the full real-estate catalog as JSON in
a single POST request. We fetch it directly over HTTP:

1. GET the `/realestate` page and locate its page chunk JS.
2. GET the chunk and extract the Server Action id (a 40+ char hex hash) that backs
   `loadServerRealEstate`. The hash changes whenever the site is redeployed, so
   we always resolve it dynamically instead of hard-coding it.
3. POST to `/realestate` with the `Next-Action: <hash>` header and the server sid
   as the single argument. The response is a React Server Components (RSC) stream;
   the catalog lives on the line prefixed with `1:`.

The catalog only ever lists *occupied* objects (every house/apartment has an
`ownerName`). "Freed" objects are detected downstream by their disappearance
between two snapshots — see `realestate_detector.py`.
"""

import re
import json
import asyncio
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

import aiohttp
from loguru import logger

from app.config import get_settings


# Server list order on wiki.gta5rp.com. The sid is the 1-based index, zero-padded
# to two digits (e.g. Murrieta is the 20th server -> sid "20"). Kept here so we
# can map a human server name to its sid without another network round-trip.
SERVER_ORDER = [
    "Downtown", "Strawberry", "Vinewood", "Blackberry", "Insquad", "Sunrise",
    "Rainbow", "Richman", "Eclipse", "La Mesa", "Burton", "Rockford", "Alta",
    "Del Perro", "Davis", "Harmony", "Redwood", "Hawick", "Grapeseed",
    "Murrieta", "Vespucci", "Milton", "La Puerta", "Senora",
]


def server_name_to_sid(name: str) -> Optional[str]:
    """Map a server display name to its zero-padded sid, e.g. 'Murrieta' -> '20'."""
    for idx, srv in enumerate(SERVER_ORDER):
        if srv.lower() == name.strip().lower():
            return f"{idx + 1:02d}"
    return None


def sid_to_server_name(sid: str) -> Optional[str]:
    """Map a zero-padded sid back to its display name, e.g. '20' -> 'Murrieta'."""
    try:
        idx = int(sid) - 1
    except (TypeError, ValueError):
        return None
    if 0 <= idx < len(SERVER_ORDER):
        return SERVER_ORDER[idx]
    return None


def resolve_servers(names: List[str]) -> Dict[str, str]:
    """Resolve display names to a {sid: name} map, dropping any unknown names.

    Order-preserving and de-duplicated by sid. Used to turn a configured server
    list into the sids the scheduler polls.
    """
    resolved: Dict[str, str] = {}
    for name in names:
        sid = server_name_to_sid(name)
        if sid and sid not in resolved:
            resolved[sid] = SERVER_ORDER[int(sid) - 1]
    return resolved


def all_wiki_servers() -> Dict[str, str]:
    """Every server the wiki `/realestate` page lists, as an ordered {sid: name}.

    This is the full pick-list offered at /start — the user may choose to track
    any of them, not just the ones pre-configured in REALESTATE_SERVERS. The
    scheduler then starts polling whichever server a user actually selects.
    """
    return {f"{idx + 1:02d}": name for idx, name in enumerate(SERVER_ORDER)}


@dataclass
class RealEstateUnit:
    """A single apartment or house entry from the catalog (an occupied object)."""
    unit_id: int
    kind: str  # "apartment" | "house"
    name: str
    price: Optional[int] = None
    class_id: Optional[str] = None
    class_name: Optional[str] = None
    owner_name: Optional[str] = None
    vehicle_count: Optional[int] = None
    house_id: Optional[int] = None      # for apartments: parent apartmentHouseId
    building_name: Optional[str] = None  # for apartments: parent building name
    image: Optional[str] = None


@dataclass
class RealEstateBuilding:
    """An apartment building aggregate (Seoul Towers, Eclipse Towers, ...)."""
    building_id: int
    name: str
    apartments_count: Optional[int] = None
    free_count: Optional[int] = None
    image: Optional[str] = None


@dataclass
class RealEstateSnapshot:
    """A full catalog snapshot for one server at one point in time."""
    server_sid: str
    apartments: List[RealEstateUnit] = field(default_factory=list)
    houses: List[RealEstateUnit] = field(default_factory=list)
    buildings: List[RealEstateBuilding] = field(default_factory=list)
    fetched_at_ms: Optional[int] = None


class RealEstateClient:
    """Fetches the `/realestate` catalog via its Next.js Server Action."""

    BASE_URL = "https://wiki.gta5rp.com"
    REALESTATE_PATH = "/realestate"

    # Matches: createServerReference)("<hex-id>",<...>,"loadServerRealEstate")
    # The action id length is not fixed (Next.js has used 40- and 42-char ids
    # across releases), so match a run of hex rather than a hard-coded length.
    _ACTION_RE = re.compile(
        r'createServerReference\)\(\s*"([0-9a-f]{40,})"[^)]*?"loadServerRealEstate"'
    )
    # Matches the realestate page chunk referenced from the HTML.
    _CHUNK_RE = re.compile(
        r'(/_next/static/chunks/app/realestate/page-[0-9a-f]+\.js)'
    )

    def __init__(self, timeout: float = 30.0):
        self.settings = get_settings()
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._action_hash: Optional[str] = None
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Accept-Language": "ru,en;q=0.9",
        }

    async def _resolve_action_hash(self, session: aiohttp.ClientSession) -> str:
        """Fetch the page + chunk and extract the loadServerRealEstate action hash."""
        async with session.get(
            f"{self.BASE_URL}{self.REALESTATE_PATH}", headers=self._headers
        ) as resp:
            resp.raise_for_status()
            html = await resp.text()

        chunk_match = self._CHUNK_RE.search(html)
        if not chunk_match:
            raise RuntimeError("Could not find realestate page chunk in HTML")
        chunk_path = chunk_match.group(1)

        async with session.get(
            f"{self.BASE_URL}{chunk_path}", headers=self._headers
        ) as resp:
            resp.raise_for_status()
            chunk_js = await resp.text()

        action_match = self._ACTION_RE.search(chunk_js)
        if not action_match:
            raise RuntimeError(
                "Could not extract loadServerRealEstate action hash from chunk"
            )
        action_hash = action_match.group(1)
        logger.info(f"Resolved realestate action hash: {action_hash}")
        return action_hash

    async def _fetch_payload(self, server_sid: str) -> Optional[Dict[str, Any]]:
        """
        Fetch the raw catalog dict for a server, transparently re-resolving the
        Server Action hash once if it has rotated (site redeploy). Returns the
        parsed catalog dict, or None if it could not be fetched.
        """
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            # Resolve (and cache) the action hash; re-resolve on failure.
            if not self._action_hash:
                self._action_hash = await self._resolve_action_hash(session)

            payload = await self._call_action(session, self._action_hash, server_sid)

            # Hash may have rotated on redeploy — retry once with a fresh one.
            if payload is None:
                logger.warning("Action call failed, re-resolving hash and retrying")
                self._action_hash = await self._resolve_action_hash(session)
                payload = await self._call_action(session, self._action_hash, server_sid)

            return payload

    async def fetch_snapshot(self, server_sid: str) -> Optional[RealEstateSnapshot]:
        """
        Fetch the full real-estate catalog for a server.

        Args:
            server_sid: Zero-padded server id, e.g. "20" for Murrieta.

        Returns:
            RealEstateSnapshot on success, None on failure.
        """
        try:
            payload = await self._fetch_payload(server_sid)
            if payload is None:
                logger.error("Failed to fetch realestate data after retry")
                return None
            return self._parse_payload(payload, server_sid)
        except Exception as e:
            logger.error(f"RealEstate fetch failed: {e}")
            return None

    async def fetch_updated_ms(self, server_sid: str) -> Optional[int]:
        """
        Fetch only the catalog's data-refresh marker (`fetchedAtMs`).

        This is the same value the map shows as "Обновлено": a server-side
        "data last refreshed at" timestamp (epoch ms) that stays constant until
        the wiki recomputes the catalog (which happens around Payday). It lets
        callers cheaply gate expensive work — a browser scrape, a full DB diff —
        on whether the data actually moved, instead of polling on a blind timer.

        Note: the endpoint returns the whole catalog (~80 KB gzip) with no-cache
        and no ETag, so the value cannot be read any cheaper than one fetch; we
        just skip parsing the 1000+ objects. Returns the marker, or None on
        failure.
        """
        try:
            payload = await self._fetch_payload(server_sid)
            return payload.get("fetchedAtMs") if payload else None
        except Exception as e:
            logger.error(f"RealEstate updated-ms fetch failed: {e}")
            return None

    async def _call_action(
        self, session: aiohttp.ClientSession, action_hash: str, server_sid: str
    ) -> Optional[Dict[str, Any]]:
        """POST the Server Action and return the parsed catalog dict (or None)."""
        headers = {
            **self._headers,
            "Next-Action": action_hash,
            "Content-Type": "text/plain;charset=UTF-8",
            "Referer": f"{self.BASE_URL}{self.REALESTATE_PATH}",
        }
        # The action takes a single positional argument: the server sid.
        body = json.dumps([server_sid])

        async with session.post(
            f"{self.BASE_URL}{self.REALESTATE_PATH}",
            headers=headers,
            data=body,
        ) as resp:
            if resp.status != 200:
                logger.warning(f"Action returned HTTP {resp.status}")
                return None
            text = await resp.text()

        return self._extract_catalog_line(text)

    @staticmethod
    def _extract_catalog_line(rsc_text: str) -> Optional[Dict[str, Any]]:
        """
        Parse the RSC stream and return the catalog JSON object.

        The stream is line-oriented, each line like `<ref>:<json>`. The catalog is
        the first line whose JSON payload contains our expected top-level keys.
        """
        for line in rsc_text.splitlines():
            if ":" not in line:
                continue
            _, _, json_part = line.partition(":")
            json_part = json_part.strip()
            if not json_part.startswith("{"):
                continue
            try:
                obj = json.loads(json_part)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and "apartmentsByHouseId" in obj:
                return obj
        return None

    @staticmethod
    def _parse_payload(
        payload: Dict[str, Any], server_sid: str
    ) -> RealEstateSnapshot:
        """Convert the raw catalog dict into a structured snapshot."""
        snapshot = RealEstateSnapshot(
            server_sid=server_sid,
            fetched_at_ms=payload.get("fetchedAtMs"),
        )

        # Houses (private homes) — flat list of occupied objects.
        for h in payload.get("houses", []) or []:
            snapshot.houses.append(RealEstateUnit(
                unit_id=h.get("id"),
                kind="house",
                name=h.get("name", ""),
                price=h.get("price"),
                class_id=h.get("classId"),
                class_name=h.get("className"),
                owner_name=h.get("ownerName"),
                vehicle_count=h.get("vehicleCount"),
                image=h.get("image"),
            ))

        # Apartment buildings (aggregates with free counts).
        buildings_by_id: Dict[int, str] = {}
        for b in payload.get("apartmentHouses", []) or []:
            bid = b.get("id")
            buildings_by_id[bid] = b.get("name", "")
            snapshot.buildings.append(RealEstateBuilding(
                building_id=bid,
                name=b.get("name", ""),
                apartments_count=b.get("apartmentsCount"),
                free_count=b.get("freeCount"),
                image=b.get("image"),
            ))

        # Individual apartments, grouped by building id.
        by_house = payload.get("apartmentsByHouseId", {}) or {}
        for house_id_key, units in by_house.items():
            try:
                house_id = int(house_id_key)
            except (ValueError, TypeError):
                house_id = None
            for a in units or []:
                parent_id = a.get("apartmentHouseId", house_id)
                snapshot.apartments.append(RealEstateUnit(
                    unit_id=a.get("id"),
                    kind="apartment",
                    name=a.get("name", ""),
                    price=a.get("price"),
                    class_id=a.get("classId"),
                    class_name=a.get("className"),
                    owner_name=a.get("ownerName"),
                    vehicle_count=a.get("vehicleCount"),
                    house_id=parent_id,
                    building_name=buildings_by_id.get(parent_id),
                    image=a.get("image"),
                ))

        logger.info(
            f"Parsed realestate snapshot: {len(snapshot.apartments)} apartments, "
            f"{len(snapshot.houses)} houses, {len(snapshot.buildings)} buildings"
        )
        return snapshot
