import asyncio, sys
sys.path.insert(0, '.')
from app.config import get_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

async def main():
    e = create_async_engine(get_settings().database.database_url)
    async with e.connect() as c:
        r = await c.execute(text("SELECT DISTINCT class_name FROM realestate_objects ORDER BY 1"))
        for row in r:
            print(row[0])
    await e.dispose()

asyncio.run(main())
