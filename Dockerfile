FROM ros:humble-ros-base-jammy

# Set working directory
WORKDIR /app

# Install system dependencies
# python3-pip is needed because ROS image is minimal
# gcc for compiling some python deps
# Explicitly install message packages to ensure they are available
RUN apt-get update && apt-get install -y \
    python3-pip \
    python3-venv \
    gcc \
    ros-humble-rmw-cyclonedds-cpp \
    ros-humble-std-msgs \
    ros-humble-sensor-msgs \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for caching)
COPY requirements.txt .

# Install Python dependencies
# Note: We use system packages for ROS 2, but pip for the rest
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p logs data

# Set environment variables
ENV FLASK_ENV=production
ENV FLASK_HOST=0.0.0.0
ENV FLASK_PORT=5050
ENV PYTHONUNBUFFERED=1

# Expose port
EXPOSE 5050

# Source ROS 2 setup in entrypoint
# We create a custom entrypoint to ensure ROS is sourced before app starts
RUN echo '#!/bin/bash\n\
source /opt/ros/humble/setup.bash\n\
exec "$@"' > /entrypoint.sh && chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]

# Run with python app.py directly to ensure main block executes (starting ROS manager thread)
# Gunicorn skips the __main__ block, so the ROS thread was never starting
CMD ["python3", "app.py"]
