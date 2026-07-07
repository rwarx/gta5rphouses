FROM python:3.12-slim-bookworm

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    curl \
    unzip \
    xvfb \
    libx11-dev \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libxss1 \
    libgtk-3-0 \
    libgbm1 \
    libasound2 \
    libnss3 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libxkbcommon0 \
    libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY docker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN python -m playwright install chromium
RUN python -m playwright install-deps chromium || echo "install-deps failed (non-critical), continuing..."

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p logs data

# Make scripts executable
RUN chmod +x docker/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/bin/bash", "/app/docker/entrypoint.sh"]
CMD ["railway"]