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
    python3-numpy \
    gcc \
    ros-humble-rmw-cyclonedds-cpp \
    ros-humble-geometry-msgs \
    ros-humble-nav-msgs \
    ros-humble-std-msgs \
    ros-humble-sensor-msgs \
    ros-humble-sensor-msgs-py \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for caching)
COPY requirements.txt .

# Install Python dependencies
# Note: We use system packages for ROS 2, but pip for the rest
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# jetson-stats: Jetson now has internet, install directly from PyPI.
# Falls back gracefully if offline (sysfs still works).
RUN pip install --no-cache-dir jetson-stats || \
    (echo "jetson-stats pip install failed, trying vendored wheels..." && \
     if [ -d /app/wheels ] && ls /app/wheels/*.whl >/dev/null 2>&1; then \
       pip install --no-cache-dir --no-index --find-links=/app/wheels jetson-stats; \
     else echo "jetson-stats not available; using sysfs fallback"; fi)

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

# A single threaded worker preserves the in-memory ROS and SSH session state while
# providing persistent WebSocket support for Flask-Sock.
CMD ["gunicorn", "--bind", "0.0.0.0:5050", "--workers", "1", "--threads", "8", "--worker-class", "gthread", "--timeout", "0", "runtime:app"]
