#!/bin/bash
set -e

# Migrate database
echo "Running database migrations..."
cd /app
alembic upgrade head

# Railway (and most PaaS) inject the public port via $PORT; fall back to 8000.
PORT="${PORT:-8000}"

# Start the application
if [ "$1" = "api" ]; then
    echo "Starting API server on port ${PORT}..."
    exec uvicorn app.api.server:create_app --host 0.0.0.0 --port "${PORT}" --factory
elif [ "$1" = "scraper" ]; then
    echo "Starting scraper service..."
    exec python -m app.run_scraper
elif [ "$1" = "bot" ]; then
    echo "Starting Telegram bot..."
    exec python -m app.run_bot
elif [ "$1" = "realestate" ]; then
    echo "Starting realestate catalog source..."
    exec python -m app.run_realestate
elif [ "$1" = "railway" ]; then
    echo "Starting API server (background, port ${PORT}) and all services..."
    uvicorn app.api.server:create_app --host 0.0.0.0 --port "${PORT}" --factory &
    exec python -m app.run_all
elif [ "$1" = "all" ]; then
    echo "Starting all services..."
    exec python -m app.run_all
else
    echo "Starting all services..."
    exec python -m app.run_all
fi