"""
Entry point for running all services.
Usage: python -m app.run_all
"""

from app.config import setup_logging

if __name__ == "__main__":
    setup_logging()
    from app.main import run_all_services
    import asyncio
    asyncio.run(run_all_services())