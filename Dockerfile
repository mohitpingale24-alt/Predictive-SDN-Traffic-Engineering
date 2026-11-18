# One-command reproducibility: dashboard + test suite in a container.
# Mininet/Ryu live experiments still run in the Linux VM (they need root and
# Open vSwitch); this image covers the dashboard, analysis and all unit tests.
FROM python:3.12-slim
WORKDIR /app
COPY requirements-controller.txt requirements-ml.txt ./
RUN pip install --no-cache-dir -r requirements-controller.txt -r requirements-ml.txt pytest
COPY . .
EXPOSE 8765
CMD ["python3", "frontend/server.py", "--port", "8765"]
