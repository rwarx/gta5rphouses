"""
Change detection for the `/realestate` catalog.

The catalog lists only *occupied* objects. We diff the freshly-fetched snapshot
against the last-known occupied set stored in the database:

  * object present now but not before  -> "occupied"        (someone bought it / new listing)
  * object present before but not now  -> "freed"           (became available to buy)
  * object present in both, owner diff:
      - outside the Payday window       -> "owner_changed"   (ordinary resale)
      - inside the Payday window         -> "possibly_freed"  (see below)

The "freed" event is the one that matters most: it is the moment a house or
apartment becomes available, which is exactly what the monitor exists to catch
as fast as possible.

"possibly_freed" exists because the map/catalog does not always refresh the
instant an object frees up. When an object's owner nickname changes *during
Payday* — the window when objects most often free and get re-bought — we treat
it as a signal the object may have just been freed (and possibly re-taken)
before a clean freed->occupied pair could be observed. Every owner change is
also appended to RealEstateOwnerHistory so the nickname timeline is preserved.
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.repository import RealEstateRepository
from app.scraper.realestate_client import RealEstateSnapshot, RealEstateUnit


@dataclass
class RealEstateChange:
    """A single detected transition in the catalog."""
    object_key: str
    event_type: str  # freed | occupied | owner_changed | possibly_freed
    kind: str
    name: str
    old_owner: Optional[str] = None
    new_owner: Optional[str] = None


class RealEstateDetector:
    """Diffs a catalog snapshot against DB state and records events."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = RealEstateRepository(session)

    async def process_snapshot(
        self, snapshot: RealEstateSnapshot, is_payday: bool = False
    ) -> List[RealEstateChange]:
        """
        Diff a snapshot against stored state, persist updates and events.

        Args:
            snapshot: The freshly-fetched catalog snapshot.
            is_payday: Whether this tick ran inside the Payday window. An owner
                change during Payday is recorded as "possibly_freed" instead of
                "owner_changed".

        Returns the list of detected changes (may be empty).
        """
        server_sid = snapshot.server_sid
        changes: List[RealEstateChange] = []

        # Keep the per-building rollup fresh for catalog listings.
        await self._persist_buildings(snapshot)

        # Flatten houses + apartments into one keyed dict of current occupants.
        current: Dict[str, RealEstateUnit] = {}
        for unit in [*snapshot.houses, *snapshot.apartments]:
            if unit.unit_id is None:
                continue
            key = self.repo.make_key(server_sid, unit.kind, unit.unit_id)
            current[key] = unit

        # A snapshot with zero objects is almost certainly a fetch/parse failure;
        # skip it so we don't emit a flood of false "freed" events.
        if not current:
            logger.warning(
                f"RealEstate snapshot for server {server_sid} has no objects, skipping diff"
            )
            return changes

        previous = await self.repo.get_occupied_keys(server_sid)
        is_first_run = len(previous) == 0

        # 1. New / still-present objects.
        for key, unit in current.items():
            prev_obj = previous.get(key)
            # Capture the old owner *before* upsert: prev_obj is the same
            # identity-mapped instance upsert_occupied is about to mutate.
            prev_owner = prev_obj.owner_name if prev_obj is not None else None
            await self.repo.upsert_occupied(key, self._unit_to_data(server_sid, unit))

            if prev_obj is None and not is_first_run:
                # Newly appeared occupied object (bought / freshly listed as owned).
                await self.repo.create_event(
                    self._event_data(server_sid, unit, "occupied", new_owner=unit.owner_name)
                )
                await self.repo.add_owner_history(
                    object_key=key,
                    server_sid=server_sid,
                    kind=unit.kind,
                    owner_name=unit.owner_name,
                    previous_owner=None,
                    during_payday=is_payday,
                )
                changes.append(RealEstateChange(
                    object_key=key, event_type="occupied", kind=unit.kind,
                    name=unit.name or "", new_owner=unit.owner_name,
                ))
            elif prev_obj is not None and (prev_owner or None) != (unit.owner_name or None):
                # Owner changed. If the new owner is empty the object freed.
                if not unit.owner_name:
                    event_type = "freed"
                    await self.repo.mark_freed(key)
                else:
                    # If the previous owner held it < 24 h, it's likely a
                    # nickname change or 5VITO sale, not a real free.
                    short_hold = False
                    if is_payday:
                        td = await self.repo.get_ownership_duration(
                            key, prev_owner, datetime.now(timezone.utc)
                        )
                        if td and td.total_seconds() < 86400:
                            short_hold = True
                    event_type = "possibly_freed" if (is_payday and not short_hold) else "owner_changed"
                    await self.repo.add_owner_history(
                        object_key=key,
                        server_sid=server_sid,
                        kind=unit.kind,
                        owner_name=unit.owner_name,
                        previous_owner=prev_owner,
                        during_payday=is_payday,
                    )
                await self.repo.create_event(
                    self._event_data(
                        server_sid, unit, event_type,
                        old_owner=prev_owner, new_owner=unit.owner_name,
                    )
                )
                changes.append(RealEstateChange(
                    object_key=key, event_type=event_type, kind=unit.kind,
                    name=unit.name or "", old_owner=prev_owner,
                    new_owner=unit.owner_name,
                ))

        # 2. Objects that disappeared from the catalog.
        #    A Престиж house vanishing during a Payday recompute is actually a
        #    conversion to a mansion, not a real free — record it as "converted"
        #    so the notifier suppresses the false-alarm ping.
        current_keys = set(current.keys())
        for key, prev_obj in previous.items():
            if key in current_keys:
                continue
            is_conversion = (
                is_payday
                and prev_obj.kind == "house"
                and prev_obj.class_name == "Престиж"
            )
            event_type = "converted" if is_conversion else "freed"
            # Still mark as freed in DB so it doesn't re-trigger next diff
            await self.repo.mark_freed(key)
            await self.repo.create_event({
                "object_key": key,
                "server_sid": server_sid,
                "kind": prev_obj.kind,
                "event_type": event_type,
                "name": prev_obj.name,
                "price": prev_obj.price,
                "class_name": prev_obj.class_name,
                "building_name": prev_obj.building_name,
                "old_owner": prev_obj.owner_name,
            })
            changes.append(RealEstateChange(
                object_key=key, event_type=event_type, kind=prev_obj.kind,
                name=prev_obj.name or "", old_owner=prev_obj.owner_name,
            ))

        logger.info(
            f"RealEstate diff (server {server_sid}): {len(changes)} change(s), "
            f"{len(current)} occupied now, {len(previous)} before"
            + (" [first run, baseline only]" if is_first_run else "")
        )
        return changes

    async def generate_snapshot_diff_frees(
        self, server_sid: str,
        pre_snapshot: List[Dict[str, Any]],
        current_snapshot: RealEstateSnapshot,
    ) -> List[RealEstateChange]:
        """Diff a pre-payday snapshot against the current catalog → 100% freed.

        Objects present in *pre_snapshot* but absent from *current_snapshot*
        (i.e. they disappeared from the catalog) are confirmed frees. Skips
        Престиж→mansion conversions.
        """
        changes: List[RealEstateChange] = []
        current_keys: set = set()
        for unit in [*current_snapshot.houses, *current_snapshot.apartments]:
            if unit.unit_id is None:
                continue
            key = self.repo.make_key(server_sid, unit.kind, unit.unit_id)
            current_keys.add(key)

        for entry in pre_snapshot:
            key = entry["object_key"]
            if key in current_keys:
                continue
            # Check if already recorded as freed today (avoid duplicates)
            existing = await self.repo.get_events_since(
                since=datetime.now(timezone.utc) - timedelta(hours=2),
                event_types=["freed", "converted"],
                server_sid=server_sid,
            )
            if any(e.object_key == key for e in existing):
                continue
            # Skip Престиж→mansion conversions
            if entry.get("class_name") == "Престиж":
                continue
            await self.repo.mark_freed(key)
            data = {
                "object_key": key,
                "server_sid": server_sid,
                "kind": entry.get("kind", "house"),
                "event_type": "freed",
                "name": entry.get("name"),
                "price": entry.get("price"),
                "class_name": entry.get("class_name"),
                "building_name": entry.get("building_name"),
                "old_owner": entry.get("owner_name"),
            }
            await self.repo.create_event(data)
            changes.append(RealEstateChange(
                object_key=key, event_type="freed",
                kind=entry.get("kind", "house"),
                name=entry.get("name") or "",
                old_owner=entry.get("owner_name"),
            ))

        if changes:
            logger.info(
                f"RealEstate snapshot-diff (server {server_sid}): "
                f"{len(changes)} confirmed freed"
            )
        return changes

    async def _persist_buildings(self, snapshot: RealEstateSnapshot) -> None:
        """Refresh the per-building rollup (free/total counts) for the catalog."""
        for b in snapshot.buildings:
            if b.building_id is None:
                continue
            key = self.repo.make_building_key(snapshot.server_sid, b.building_id)
            await self.repo.upsert_building(key, {
                "server_sid": snapshot.server_sid,
                "building_id": b.building_id,
                "name": b.name,
                "apartments_count": b.apartments_count,
                "free_count": b.free_count,
                "image": b.image,
            })

    @staticmethod
    def _unit_to_data(server_sid: str, unit: RealEstateUnit) -> Dict[str, Any]:
        """Map a catalog unit to RealEstateObject column values."""
        return {
            "server_sid": server_sid,
            "kind": unit.kind,
            "unit_id": unit.unit_id,
            "name": unit.name,
            "price": unit.price,
            "class_name": unit.class_name,
            "owner_name": unit.owner_name,
            "vehicle_count": unit.vehicle_count,
            "building_name": unit.building_name,
            "image": unit.image,
        }

    @staticmethod
    def _event_data(
        server_sid: str,
        unit: RealEstateUnit,
        event_type: str,
        old_owner: Optional[str] = None,
        new_owner: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build a RealEstateEvent row from a catalog unit."""
        return {
            "object_key": RealEstateRepository.make_key(server_sid, unit.kind, unit.unit_id),
            "server_sid": server_sid,
            "kind": unit.kind,
            "event_type": event_type,
            "name": unit.name,
            "price": unit.price,
            "class_name": unit.class_name,
            "building_name": unit.building_name,
            "old_owner": old_owner,
            "new_owner": new_owner,
        }
