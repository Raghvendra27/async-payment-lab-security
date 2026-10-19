FROM python:3.11-slim

# Install network reconnaissance tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    nmap \
    netcat-openbsd \
    curl \
    dnsutils \
    iputils-ping \
    iproute2 \
    tcpdump \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /attack

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tools/ ./tools/

# Keep container running for interactive use
CMD ["bash"]
