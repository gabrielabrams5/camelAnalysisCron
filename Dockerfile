# Use Python 3.11 slim base image
FROM python:3.11-slim

# Install cron and procps (for pgrep)
RUN apt-get update && apt-get install -y cron procps && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY analyze.py .
COPY luma_sync.py .
COPY import_luma_attendance.py .
COPY run_luma_pipeline.sh .

# Create output directory for analysis files (will be mounted as volume)
RUN mkdir -p /app/analysis_outputs

# Copy entrypoint script
COPY entrypoint.py /app/entrypoint.py
RUN chmod +x /app/entrypoint.py

# Make pipeline script executable
RUN chmod +x /app/run_luma_pipeline.sh

# Create cron job file
# Run every 6 hours: 0 */6 * * * (or customize as needed)
RUN echo "0 */6 * * * cd /app && /bin/bash /app/run_luma_pipeline.sh >> /var/log/cron.log 2>&1" > /etc/cron.d/analytics-cron

# Give execution rights on the cron job
RUN chmod 0644 /etc/cron.d/analytics-cron

# Apply cron job
RUN crontab /etc/cron.d/analytics-cron

# Create the log file to be able to run tail
RUN touch /var/log/cron.log

# Run the entrypoint script
CMD ["python3", "/app/entrypoint.py"]
