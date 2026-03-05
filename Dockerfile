# Use Python 3.11 slim base image
FROM python:3.11-slim

# Install system dependencies, Node.js, and Chromium
RUN apt-get update && apt-get install -y \
    # Existing dependencies
    cron \
    procps \
    # Dependencies for Node.js installation
    curl \
    gnupg \
    ca-certificates \
    # Chromium and dependencies for Puppeteer
    chromium \
    chromium-sandbox \
    # Required libraries for Chromium
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libwayland-client0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 20.x LTS
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Set Puppeteer environment variable to use system Chromium
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY analyze.py .
COPY luma_sync.py .
COPY import_luma_attendance.py .
COPY event_analysis_single.py .
COPY generate_all_placards.py .
COPY transform_to_placard_csv.py .
COPY run_luma_pipeline.sh .

# Copy directories
COPY luma/ /app/luma/
COPY mailChimp/ /app/mailChimp/
COPY placard_generation/ /app/placard_generation/

# Install Node.js dependencies for placard generation
WORKDIR /app/placard_generation
RUN npm ci
WORKDIR /app

# Create output directory for analysis files (will be mounted as volume)
RUN mkdir -p /app/analysis_outputs

# Copy entrypoint script
COPY entrypoint.py /app/entrypoint.py
RUN chmod +x /app/entrypoint.py

# Make pipeline script executable
RUN chmod +x /app/run_luma_pipeline.sh

# Create the log file to be able to run tail
# Note: Crontab is generated dynamically by entrypoint.py with environment variables
RUN touch /var/log/cron.log

# Run the entrypoint script
CMD ["python3", "/app/entrypoint.py"]
