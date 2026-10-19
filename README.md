# AsyncPay Lab — Distributed Financial System Security Lab

A deliberately vulnerable asynchronous payment processing system built for
security research and penetration testing practice.

The system simulates a simplified UPI-like payment architecture where services
communicate asynchronously through a message broker. It contains **intentional
security weaknesses** across multiple layers — your job is to find and exploit them,
then implement fixes.

---

## Architecture

```
                    ┌─── PUBLIC NETWORK (172.20.0.0/24) ───┐
                    │                                       │
  User / Attacker ──┤──► GATEWAY (FastAPI :8080)            │
                    │        │                              │
                    └────────┼──────────────────────────────┘
                             │ publishes to queue
                    ┌────────┼── INTERNAL NETWORK (172.21.0.0/24) ──┐
                    │        ▼                                       │
                    │   RABBITMQ (:5672 AMQP / :15672 Mgmt UI)      │
                    │     │  ▲  │                                    │
                    │     │  │  │    async message flow              │
                    │     ▼  │  ▼                                    │
                    │   ORCHESTRATOR          BANK SERVICE           │
                    │   (saga coordinator)    (debit / credit)       │
                    │                              │                 │
                    │                              ▼                 │
                    │                        POSTGRESQL (:5432)      │
                    │                        (account ledger)        │
                    └────────────────────────────────────────────────┘
```

### Async Message Flow (Payment Lifecycle)

```
1. POST /api/v1/payment ──────────► Gateway
2. Gateway ──[transactions]──────► RabbitMQ
3.    ◄── returns {txn_id, "ACCEPTED"} immediately (async!)
4. Orchestrator ◄──[transactions]── RabbitMQ
5. Orchestrator ──[debit_requests]──► RabbitMQ
6. Bank Service ◄──[debit_requests]── RabbitMQ
7.    Bank checks balance → debits account → commits to PostgreSQL
8. Bank Service ──[transaction_results {DEBIT_COMPLETED}]──► RabbitMQ
9. Orchestrator ◄──[transaction_results]── RabbitMQ
10. Orchestrator ──[credit_requests]──► RabbitMQ
11. Bank Service ◄──[credit_requests]── RabbitMQ
12.    Bank credits receiver → commits to PostgreSQL
13. Bank Service ──[transaction_results {CREDIT_COMPLETED}]──► RabbitMQ
14. Orchestrator ◄── marks transaction COMPLETED
```

The entire flow from step 2 onward is **asynchronous**. The user gets a
response at step 3 before any money has moved.

---

## Prerequisites

- Docker Engine 20.10+
- Docker Compose v2+

### Install on Arch Linux

```bash
sudo pacman -S docker docker-compose
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
# Log out and back in for group change to take effect
```

---

## Quick Start

```bash
# Clone / copy the project
cd async-payment-lab

# Build and start all services
docker compose up --build -d

# Watch logs (all services)
docker compose logs -f

# Check everything is healthy
docker compose ps
```

Wait ~30 seconds for RabbitMQ and PostgreSQL to initialize.

### Verify the System

```bash
# Health check
curl http://localhost:8080/health

# List accounts (use the test API key)
curl -H "X-Api-Key: test-key-123" http://localhost:8080/api/v1/accounts

# Make a test payment
curl -X POST http://localhost:8080/api/v1/payment \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: test-key-123" \
  -d '{"sender_account":"ACCT-1001","receiver_account":"ACCT-2001","amount":500}'

# Check balances after a few seconds
curl -H "X-Api-Key: test-key-123" http://localhost:8080/api/v1/accounts

# View transaction log
curl -H "X-Api-Key: test-key-123" http://localhost:8080/api/v1/transactions

# RabbitMQ Management UI
# Open http://localhost:15672 in browser (guest / guest)
```

### Test Accounts

| Account    | Balance    | Bank   |
|------------|------------|--------|
| ACCT-1001  | ₹10,000    | BANK-A |
| ACCT-1002  | ₹5,000     | BANK-A |
| ACCT-1003  | ₹2,500     | BANK-A |
| ACCT-2001  | ₹8,000     | BANK-B |
| ACCT-2002  | ₹3,000     | BANK-B |
| ACCT-2003  | ₹15,000    | BANK-B |

---

## Scaling for Race Conditions

Run multiple bank service instances to widen the race window:

```bash
docker compose up --scale bank_service=3 -d
```

---

## Using the Attacker Container

```bash
# Get a shell in the attacker container
docker compose exec attacker bash

# Run reconnaissance
python3 tools/recon.py

# Manual exploration
curl http://gateway:8080/api/v1/accounts -H "X-Api-Key: test-key-123"
nmap -sV rabbitmq
nmap -sV postgres
```

---

## Resetting the Lab

```bash
# Full reset (destroys all data)
docker compose down -v
docker compose up --build -d
```

---

## Security Testing Phases

1. **Reconnaissance** — Discover services, open ports, default credentials
2. **Vulnerability Assessment** — Identify weaknesses in the architecture and code
3. **Exploitation** — Build and run attack scripts
4. **Remediation** — Implement fixes and verify they work
5. **Report** — Document findings in a professional vulnerability assessment

The system has intentional weaknesses across:
- Authentication & authorization
- Message integrity & confidentiality
- Concurrency & atomicity
- Idempotency & replay protection
- Network segmentation
- Data exposure & access control

Good hunting.
