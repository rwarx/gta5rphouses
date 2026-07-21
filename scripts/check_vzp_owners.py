"""
Cross-reference VZP battle participants with realestate owners.
Run via: railway run python scripts/check_vzp_owners.py
"""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ['BOT_PASSWORD'] = 'lovlyanaxuy22811'

from app.config import get_settings
settings = get_settings()

import aiohttp
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import text

async def main():
    # Connect to DB
    engine = create_async_engine(settings.database.database_url, echo=False)
    
    async with engine.connect() as conn:
        # Get all distinct owners
        result = await conn.execute(
            text("SELECT DISTINCT owner_name FROM realestate_objects WHERE is_occupied = true AND owner_name IS NOT NULL")
        )
        db_owners = {row[0] for row in result}
    
    print(f'Owners in realestate DB: {len(db_owners)}')
    
    # Fetch VZP events
    async with aiohttp.ClientSession() as session:
        async with session.get('https://vzp-gta5rp.com/api/events?limit=100&offset=0') as resp:
            events = await resp.json()
    
    completed = [e for e in events if e.get('endedAt') and e.get('isAttackerWin') is not None]
    print(f'VZP events: {len(events)} total, {len(completed)} completed')
    
    # Get participants from completed events (last 24h)
    all_vzp_players = set()
    async with aiohttp.ClientSession() as session:
        for e in completed[:30]:
            async with session.get(f'https://vzp-gta5rp.com/api/events/{e["eventId"]}') as resp:
                detail = await resp.json()
            for p in (detail.get('attackers') or []) + (detail.get('defenders') or []):
                name = p.get('charName', '').strip()
                if name:
                    all_vzp_players.add(name)
    
    print(f'Unique VZP participants: {len(all_vzp_players)}')
    
    # Cross-reference
    matched = db_owners & all_vzp_players
    unmatched = db_owners - all_vzp_players
    
    print(f'\n=== Cross-reference results ===')
    print(f'Owners seen in VZP (recently online): {len(matched)} ({len(matched)/len(db_owners)*100:.1f}%)' if db_owners else 'N/A')
    print(f'Owners NOT seen in VZP (potentially offline): {len(unmatched)} ({len(unmatched)/len(db_owners)*100:.1f}%)' if db_owners else 'N/A')
    
    if matched:
        print(f'\nSample matched owners: {list(matched)[:10]}')
    if unmatched:
        print(f'\nSample unmatched owners: {list(unmatched)[:10]}')
    
    print(f'\nMatched owners: {sorted(matched)}')
    
    await engine.dispose()

asyncio.run(main())
