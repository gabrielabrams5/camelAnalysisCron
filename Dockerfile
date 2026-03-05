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
COPY event_analysis_single.py .
COPY generate_all_placards.py .
COPY transform_to_placard_csv.py .
COPY run_luma_pipeline.sh .

# Copy directories
COPY luma/ /app/luma/
COPY mailChimp/ /app/mailChimp/
COPY placard_generation/ /app/placard_generation/

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
