#!/usr/bin/env python3
"""
AsyncPay Lab — Reconnaissance Tool

Phase 1 of the security assessment:
  Discover services, open ports, default credentials,
  and exposed APIs across the lab network.

Usage:
  python3 tools/recon.py
"""

import socket
import json
import sys
from datetime import datetime

import requests
import pika

# ── Known targets from the Docker network ──
TARGETS = {
    "gateway":      {"host": "gateway",    "ports": [8080]},
    "rabbitmq":     {"host": "rabbitmq",   "ports": [5672, 15672, 25672]},
    "postgres":     {"host": "postgres",   "ports": [5432]},
    "orchestrator": {"host": "orchestrator","ports": [8000, 8080, 5672]},
}

# Common default credentials to test
DEFAULT_CREDS = [
    ("guest", "guest"),
    ("admin", "admin"),
    ("root", "root"),
    ("bankuser", "bankpass"),
]

GATEWAY_API_KEYS = [
    "test-key-123",
    "merchant-key-456",
    "admin-key",
    "api-key-001",
]

DIVIDER = "=" * 60


def banner():
    print(f"""
{DIVIDER}
  AsyncPay Lab — Reconnaissance Report
  Generated: {datetime.now().isoformat()}
{DIVIDER}
""")


def scan_port(host, port, timeout=2):
    """Check if a TCP port is open."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except (socket.gaierror, socket.error):
        return False


def phase_network_discovery():
    """Scan known hosts for open ports."""
    print("[PHASE 1] Network Discovery — Port Scanning")
    print("-" * 40)

    findings = {}
    for name, info in TARGETS.items():
        host = info["host"]
        open_ports = []
        for port in info["ports"]:
            if scan_port(host, port):
                open_ports.append(port)
                print(f"  [OPEN]   {name:15s}  {host}:{port}")
            else:
                print(f"  [CLOSED] {name:15s}  {host}:{port}")
        findings[name] = open_ports

    # Extended port scan on each host
    print(f"\n  Extended scan (common service ports)...")
    extra_ports = [80, 443, 3000, 3306, 6379, 8443, 9090, 9200, 27017]
    for name, info in TARGETS.items():
        for port in extra_ports:
            if port not in info["ports"] and scan_port(info["host"], port, timeout=1):
                print(f"  [FOUND]  {name:15s}  {info['host']}:{port}  (unexpected)")
                findings.setdefault(name, []).append(port)

    print()
    return findings


def phase_credential_testing():
    """Test default credentials on discovered services."""
    print("[PHASE 2] Credential Testing")
    print("-" * 40)

    vulns = []

    # Test RabbitMQ credentials
    print("  Testing RabbitMQ AMQP credentials...")
    for user, passwd in DEFAULT_CREDS:
        try:
            conn = pika.BlockingConnection(
                pika.ConnectionParameters(
                    host="rabbitmq",
                    credentials=pika.PlainCredentials(user, passwd),
                    connection_attempts=1,
                    retry_delay=0,
                    socket_timeout=3,
                )
            )
            conn.close()
            finding = f"RabbitMQ AMQP: {user}:{passwd}"
            print(f"  [VULN]   {finding}")
            vulns.append(finding)
        except Exception:
            print(f"  [FAIL]   RabbitMQ AMQP: {user}:{passwd}")

    # Test RabbitMQ Management API
    print("\n  Testing RabbitMQ Management API...")
    for user, passwd in DEFAULT_CREDS:
        try:
            r = requests.get(
                "http://rabbitmq:15672/api/overview",
                auth=(user, passwd),
                timeout=3,
            )
            if r.status_code == 200:
                data = r.json()
                finding = (
                    f"RabbitMQ Management API: {user}:{passwd} "
                    f"(version: {data.get('rabbitmq_version', '?')})"
                )
                print(f"  [VULN]   {finding}")
                vulns.append(finding)
        except Exception:
            pass

    # Test Gateway API keys
    print("\n  Testing Gateway API keys...")
    for key in GATEWAY_API_KEYS:
        try:
            r = requests.get(
                "http://gateway:8080/api/v1/accounts",
                headers={"X-Api-Key": key},
                timeout=3,
            )
            if r.status_code == 200:
                finding = f"Gateway API key valid: {key}"
                print(f"  [VULN]   {finding}")
                vulns.append(finding)
                accounts = r.json()
                print(f"           → Exposed {len(accounts)} accounts")
        except Exception:
            pass

    # Test PostgreSQL credentials
    print("\n  Testing PostgreSQL credentials...")
    try:
        import psycopg2

        for user, passwd in DEFAULT_CREDS + [("bankuser", "bankpass")]:
            try:
                conn = psycopg2.connect(
                    host="postgres",
                    database="bankdb",
                    user=user,
                    password=passwd,
                    connect_timeout=3,
                )
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM accounts")
                count = cur.fetchone()[0]
                conn.close()
                finding = f"PostgreSQL: {user}:{passwd} ({count} accounts)"
                print(f"  [VULN]   {finding}")
                vulns.append(finding)
            except Exception:
                print(f"  [FAIL]   PostgreSQL: {user}:{passwd}")
    except ImportError:
        print("  [SKIP]   psycopg2 not installed")

    print()
    return vulns


def phase_api_enumeration():
    """Enumerate the Gateway API endpoints and extract data."""
    print("[PHASE 3] API Enumeration")
    print("-" * 40)

    base = "http://gateway:8080"
    key = "test-key-123"
    headers = {"X-Api-Key": key}
    findings = []

    # Check common endpoints
    endpoints = [
        "/health",
        "/docs",         # FastAPI auto-generated docs
        "/openapi.json", # OpenAPI spec
        "/api/v1/accounts",
        "/api/v1/transactions",
    ]

    for ep in endpoints:
        try:
            r = requests.get(f"{base}{ep}", headers=headers, timeout=3)
            status = r.status_code
            size = len(r.content)
            print(f"  [{status}]  {ep:30s}  ({size} bytes)")
            if status == 200:
                findings.append({"endpoint": ep, "status": status, "size": size})
        except Exception as e:
            print(f"  [ERR]  {ep:30s}  {e}")

    # If we found accounts, list them
    try:
        r = requests.get(f"{base}/api/v1/accounts", headers=headers, timeout=3)
        if r.status_code == 200:
            accounts = r.json()
            print(f"\n  Account data exposed via API:")
            for acct in accounts:
                print(
                    f"    {acct['account_id']:12s}  "
                    f"₹{acct['balance']:>12,.2f}  "
                    f"{acct['bank_code']}"
                )
    except Exception:
        pass

    print()
    return findings


def phase_queue_enumeration():
    """Enumerate RabbitMQ queues and their message counts."""
    print("[PHASE 4] Message Queue Enumeration")
    print("-" * 40)

    findings = []

    try:
        r = requests.get(
            "http://rabbitmq:15672/api/queues",
            auth=("guest", "guest"),
            timeout=5,
        )
        if r.status_code == 200:
            queues = r.json()
            for q in queues:
                name = q.get("name", "?")
                msgs = q.get("messages", 0)
                consumers = q.get("consumers", 0)
                print(
                    f"  Queue: {name:25s}  "
                    f"messages={msgs:4d}  "
                    f"consumers={consumers}"
                )
                findings.append({
                    "queue": name,
                    "messages": msgs,
                    "consumers": consumers,
                })
    except Exception as e:
        print(f"  [ERR] Could not enumerate queues: {e}")

    # Check for exchanges
    try:
        r = requests.get(
            "http://rabbitmq:15672/api/exchanges",
            auth=("guest", "guest"),
            timeout=5,
        )
        if r.status_code == 200:
            exchanges = r.json()
            print(f"\n  Exchanges:")
            for ex in exchanges:
                name = ex.get("name", "(default)")
                etype = ex.get("type", "?")
                if name:
                    print(f"    {name:25s}  type={etype}")
    except Exception:
        pass

    print()
    return findings


def summary(net_findings, cred_vulns, api_findings, queue_findings):
    """Print attack surface summary."""
    print(DIVIDER)
    print("  ATTACK SURFACE SUMMARY")
    print(DIVIDER)

    total_open = sum(len(ports) for ports in net_findings.values())
    print(f"  Open ports discovered:    {total_open}")
    print(f"  Credential vulns found:   {len(cred_vulns)}")
    print(f"  API endpoints accessible: {len(api_findings)}")
    print(f"  Message queues found:     {len(queue_findings)}")

    print(f"\n  Potential attack vectors:")
    print(f"    1. Race condition via concurrent Gateway requests")
    print(f"    2. Message replay via RabbitMQ access")
    print(f"    3. Queue injection (forge debit/credit messages)")
    print(f"    4. Direct database manipulation via exposed PostgreSQL")
    print(f"    5. API abuse (no rate limiting, no per-account auth)")
    print(f"    6. Man-in-the-middle (no TLS on internal traffic)")
    print()


def main():
    banner()
    net = phase_network_discovery()
    creds = phase_credential_testing()
    api = phase_api_enumeration()
    queues = phase_queue_enumeration()
    summary(net, creds, api, queues)


if __name__ == "__main__":
    main()
