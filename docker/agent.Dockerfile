FROM python:3.12-slim

# Install tcpdump and util-linux (provides nsenter)
RUN apt-get update && apt-get install -y --no-install-recommends \
        tcpdump \
        util-linux \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy agent source code
COPY agent/ .

# Directory where pcap files are written on the node (mounted via hostPath or emptyDir)
ENV CAPTURE_BASE_DIR=/captures

EXPOSE 8081

CMD ["python", "main.py"]
