"""
Entry point for running only the /realestate catalog source.
Usage: python -m app.run_realestate
"""

import asyncio
from app.config import setup_logging

if __name__ == "__main__":
    setup_logging()
    from app.main import run_realestate_only
    asyncio.run(run_realestate_only())
