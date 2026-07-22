"""Clean up old Престиж freed events that were actually conversions."""
import os, asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def main():
    raw = os.environ["DATABASE_URL"]
    url = raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        result = await conn.execute(
            text("""
                DELETE FROM realestate_events
                WHERE event_type = 'freed'
                  AND kind = 'house'
                  AND class_name = 'Престиж'
            """)
        )
        await conn.commit()
        print(f"Deleted {result.rowcount} old Престиж freed rows")
        # also show remaining counts
        rs = await conn.execute(
            text("""
                SELECT event_type, kind, class_name, COUNT(*) as cnt
                FROM realestate_events
                GROUP BY event_type, kind, class_name
                ORDER BY event_type, kind, class_name
            """)
        )
        for row in rs:
            print(f"  {row.event_type:12s} {row.kind:10s} {(row.class_name or '-'):10s} {row.cnt}")

    await engine.dispose()

asyncio.run(main())
