# One-command reproducibility: dashboard + test suite in a container.
# Mininet/Ryu live experiments still run in the Linux VM (they need root and
# Open vSwitch); this image covers the dashboard, analysis and all unit tests.
FROM python:3.12-slim
WORKDIR /app
COPY requirements-dashboard.txt ./
RUN pip install --no-cache-dir -r requirements-dashboard.txt pytest
COPY . .
EXPOSE 8765
CMD ["python3", "frontend/server.py", "--host", "0.0.0.0", "--port", "8765"]
