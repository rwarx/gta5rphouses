"""
API routes for Apartment Checker.
Provides REST endpoints for frontend and external access.
"""

from typing import Optional, List
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.session import get_db_session
from app.database.repository import (
    ApartmentRepository,
    ApartmentHistoryRepository,
    ChangeRepository,
    ScraperLogRepository,
    NotificationRepository,
    RealEstateRepository,
)
from app.database.models import Apartment

router = APIRouter(prefix="/api/v1", tags=["apartments"])


async def require_admin_token(
    authorization: Optional[str] = Header(None),
) -> None:
    """Guard side-effecting endpoints with a bearer token.

    If API_ADMIN_TOKEN is unset the endpoint is disabled entirely (503) rather
    than left open — side-effecting routes must never be callable anonymously.
    """
    configured = get_settings().api.admin_token
    if not configured:
        raise HTTPException(
            status_code=503,
            detail="Endpoint disabled: set API_ADMIN_TOKEN to enable it.",
        )
    expected = f"Bearer {configured}"
    # Constant-time compare to avoid leaking the token via timing.
    import hmac
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing token.")


# ============ Apartment endpoints ============

@router.get("/apartments")
async def get_apartments(
    search: Optional[str] = Query(None, description="Search query"),
    free_only: bool = Query(False, description="Only show apartments with free units"),
    session: AsyncSession = Depends(get_db_session),
):
    """Get all apartments with optional filtering."""
    repo = ApartmentRepository(session)

    if search:
        apartments = await repo.search(search)
    elif free_only:
        apartments = await repo.get_free_apartments()
    else:
        apartments = await repo.get_all()

    return [
        {
            "id": a.id,
            "apartment_id": a.apartment_id,
            "name": a.name,
            "address": a.address,
            "total_apartments": a.total_apartments,
            "free_apartments": a.free_apartments,
            "occupied_apartments": a.occupied_apartments,
            "description": a.description,
            "wiki_url": a.wiki_url,
            "last_updated": a.last_updated.isoformat() if a.last_updated else None,
            "is_active": a.is_active,
            "types": [
                {
                    "class_name": t.class_name,
                    "total": t.total,
                    "free": t.free,
                    "occupied": t.occupied,
                }
                for t in a.apartment_types
            ] if hasattr(a, "apartment_types") else [],
        }
        for a in apartments
    ]


@router.get("/apartments/{apartment_id}")
async def get_apartment_detail(
    apartment_id: int,
    session: AsyncSession = Depends(get_db_session),
):
    """Get detailed information about a specific apartment."""
    repo = ApartmentRepository(session)
    apartment = await repo.get_with_types(apartment_id)

    if not apartment:
        raise HTTPException(status_code=404, detail="Apartment not found")

    return {
        "id": apartment.id,
        "apartment_id": apartment.apartment_id,
        "name": apartment.name,
        "address": apartment.address,
        "total_apartments": apartment.total_apartments,
        "free_apartments": apartment.free_apartments,
        "occupied_apartments": apartment.occupied_apartments,
        "description": apartment.description,
        "wiki_url": apartment.wiki_url,
        "last_updated": apartment.last_updated.isoformat() if apartment.last_updated else None,
        "is_active": apartment.is_active,
        "coordinates": apartment.coordinates,
        "raw_data": apartment.raw_data,
        "types": [
            {
                "class_name": t.class_name,
                "total": t.total,
                "free": t.free,
                "occupied": t.occupied,
            }
            for t in apartment.apartment_types
        ],
    }


# ============ History endpoints ============

@router.get("/apartments/{apartment_id}/history")
async def get_apartment_history(
    apartment_id: int,
    days: int = Query(7, description="Number of days of history"),
    session: AsyncSession = Depends(get_db_session),
):
    """Get history for a specific apartment."""
    repo = ApartmentHistoryRepository(session)
    start_date = datetime.utcnow() - timedelta(days=days)
    records = await repo.get_history_by_period(apartment_id, start_date)

    return [
        {
            "id": r.id,
            "free_apartments": r.free_apartments,
            "occupied_apartments": r.occupied_apartments,
            "source_updated_at": r.source_updated_at.isoformat() if r.source_updated_at else None,
            "recorded_at": r.recorded_at.isoformat() if r.recorded_at else None,
            "snapshot_data": r.snapshot_data,
        }
        for r in records
    ]


@router.get("/apartments/{apartment_id}/free-history")
async def get_free_history(
    apartment_id: int,
    days: int = Query(7, description="Number of days"),
    session: AsyncSession = Depends(get_db_session),
):
    """Get free apartment count history for charting."""
    repo = ApartmentHistoryRepository(session)
    history = await repo.get_free_history(apartment_id, days)

    return history


# ============ Changes endpoints ============

@router.get("/changes")
async def get_changes(
    limit: int = Query(50, description="Number of changes to return"),
    session: AsyncSession = Depends(get_db_session),
):
    """Get recent changes across all apartments."""
    repo = ChangeRepository(session)
    changes = await repo.get_recent(limit=limit)

    return [
        {
            "id": c.id,
            "apartment_id": c.apartment_id,
            "apartment_name": c.apartment.name if c.apartment else None,
            "field_name": c.field_name,
            "old_value": c.old_value,
            "new_value": c.new_value,
            "change_type": c.change_type,
            "detected_at": c.detected_at.isoformat() if c.detected_at else None,
            "notified": c.notified,
        }
        for c in changes
    ]


# ============ Statistics endpoints ============

@router.get("/statistics")
async def get_statistics(
    session: AsyncSession = Depends(get_db_session),
):
    """Get overall system statistics."""
    apt_repo = ApartmentRepository(session)
    log_repo = ScraperLogRepository(session)

    apt_stats = await apt_repo.get_statistics()
    log_stats = await log_repo.get_statistics()

    return {
        "apartments": apt_stats,
        "scraper": log_stats,
    }


# ============ Scraper endpoints ============

@router.get("/scraper/status")
async def get_scraper_status(
    session: AsyncSession = Depends(get_db_session),
):
    """Get scraper execution status and logs."""
    log_repo = ScraperLogRepository(session)
    logs = await log_repo.get_recent(limit=10)
    stats = await log_repo.get_statistics()

    return {
        "statistics": stats,
        "recent_logs": [
            {
                "id": log.id,
                "status": log.status,
                "apartments_checked": log.apartments_checked,
                "apartments_success": log.apartments_success,
                "apartments_failed": log.apartments_failed,
                "changes_detected": log.changes_detected,
                "duration_seconds": log.duration_seconds,
                "error_message": log.error_message,
                "ran_at": log.ran_at.isoformat() if log.ran_at else None,
                "is_payday_run": log.is_payday_run,
            }
            for log in logs
        ],
    }


@router.get("/export")
async def export_data(
    format: str = Query("json", description="Export format: json or csv"),
    session: AsyncSession = Depends(get_db_session),
):
    """Export apartment data in JSON or CSV format."""
    repo = ApartmentRepository(session)
    apartments = await repo.get_all()

    data = [
        {
            "name": a.name,
            "address": a.address,
            "total": a.total_apartments,
            "free": a.free_apartments,
            "occupied": a.occupied_apartments,
            "last_updated": a.last_updated.isoformat() if a.last_updated else None,
        }
        for a in apartments
    ]

    if format == "csv":
        import csv
        import io
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=data[0].keys() if data else [])
        writer.writeheader()
        writer.writerows(data)
        from fastapi.responses import StreamingResponse
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=apartments.csv"},
        )

    return data


# ============ Health check ============

@router.post("/scraper/trigger", dependencies=[Depends(require_admin_token)])
async def trigger_scrape():
    """Manually trigger a full apartment scrape (token-protected).

    Uses the process-wide scheduler singleton so it reuses the running browser
    lifecycle instead of spawning a second, unmanaged Playwright instance. This
    is a POST (not GET) because it has side effects and must not be prefetched.
    """
    from app.scraper.scheduler import get_scheduler
    scheduler = get_scheduler()
    try:
        result = await scheduler.force_scrape()
        return {
            "status": "success" if result else "partial",
            "apartments_scraped": len(result) if result else 0,
            "message": f"Scraped {len(result)} apartments" if result else "No apartments scraped",
        }
    except Exception as e:
        from loguru import logger
        logger.error(f"Manual scrape failed: {e}")
        return {"status": "error", "message": str(e)}


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "GTA5RP Apartment Checker",
    }

@router.get("/admin/longest-ownership")
async def longest_ownership(db: AsyncSession = Depends(get_db_session)):
    from app.database.repository import RealEstateRepository
    from datetime import timezone
    repo = RealEstateRepository(db)
    result = []
    for server_sid in ("09", "11", "20"):
        durations = await repo.get_all_current_ownership_durations(server_sid)
        houses = [d for d in durations if d["kind"] == "house"]
        if houses:
            longest = max(houses, key=lambda x: (x["duration"].total_seconds() if x["duration"] else 0))
            result.append({
                "server_sid": server_sid,
                "name": longest["name"],
                "owner": longest["owner_name"],
                "acquired_at": longest["acquired_at"].isoformat() if longest["acquired_at"] else None,
                "days": round(longest["duration"].total_seconds() / 86400, 1) if longest["duration"] else None,
            })
    return result


# ============ RealEstate (catalog source) endpoints ============

@router.get("/realestate/events")
async def get_realestate_events(
    limit: int = Query(50, description="Number of events to return"),
    event_type: Optional[str] = Query(None, description="Filter: freed | occupied | owner_changed"),
    session: AsyncSession = Depends(get_db_session),
):
    """Get recent events detected from the /realestate catalog."""
    repo = RealEstateRepository(session)
    events = await repo.get_recent_events(limit=limit, event_type=event_type)

    return [
        {
            "id": e.id,
            "object_key": e.object_key,
            "server_sid": e.server_sid,
            "kind": e.kind,
            "event_type": e.event_type,
            "name": e.name,
            "price": e.price,
            "class_name": e.class_name,
            "building_name": e.building_name,
            "old_owner": e.old_owner,
            "new_owner": e.new_owner,
            "detected_at": e.detected_at.isoformat() if e.detected_at else None,
            "notified": e.notified,
        }
        for e in events
    ]


@router.get("/realestate/status")
async def get_realestate_status(
    session: AsyncSession = Depends(get_db_session),
):
    """Get realestate source configuration and current occupied count."""
    from app.config import get_settings
    from app.scraper.realestate_client import server_name_to_sid

    rs = get_settings().realestate
    sid = server_name_to_sid(rs.server_name)
    repo = RealEstateRepository(session)
    occupied = await repo.count_occupied(sid) if sid else 0
    recent = await repo.get_recent_events(limit=10)

    return {
        "enabled": rs.enabled,
        "server_name": rs.server_name,
        "server_sid": sid,
        "interval": rs.interval,
        "notify_freed": rs.notify_freed,
        "occupied_objects": occupied,
        "recent_events": [
            {
                "kind": e.kind,
                "event_type": e.event_type,
                "name": e.name,
                "price": e.price,
                "detected_at": e.detected_at.isoformat() if e.detected_at else None,
            }
            for e in recent
        ],
    }