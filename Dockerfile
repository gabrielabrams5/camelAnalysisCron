# Use Python 3.11 slim base image
FROM python:3.11-slim

# Install cron
RUN apt-get update && apt-get install -y cron && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY analyze.py .
COPY .env .

# Create output directory for analysis files (will be mounted as volume)
RUN mkdir -p /app/analysis_outputs

# Create cron job file
# Run weekly on Sunday at midnight UTC (0 0 * * 0)
RUN echo "0 0 * * 0 cd /app && /usr/local/bin/python /app/analyze.py --outdir /app/analysis_outputs >> /var/log/cron.log 2>&1" > /etc/cron.d/analytics-cron

# Give execution rights on the cron job
RUN chmod 0644 /etc/cron.d/analytics-cron

# Apply cron job
RUN crontab /etc/cron.d/analytics-cron

# Create the log file to be able to run tail
RUN touch /var/log/cron.log

# Run cron in the foreground and tail the log file
CMD cron && tail -f /var/log/cron.log
