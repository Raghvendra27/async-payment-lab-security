"""
AsyncPay Gateway — Synchronous entry point for the payment system.

Accepts payment requests via HTTP, validates them, assigns a transaction ID,
and drops the request onto the RabbitMQ message queue for async processing.
Returns immediately with a "PROCESSING" status.

Also provides read endpoints for account balances and transaction history.
"""

import os
import json
import uuid
import time
import logging

import pika
import psycopg2
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="[GATEWAY] %(asctime)s  %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="AsyncPay Gateway", version="1.0.0")

# --- Configuration ---
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres")

# Hardcoded credentials — no rotation, no vault, no per-merchant scoping
API_KEYS = {"test-key-123", "merchant-key-456"}


# --- Models ---
class PaymentRequest(BaseModel):
    sender_account: str
    receiver_account: str
    amount: float


# --- Connections ---
def get_rabbit():
    """Plain AMQP — no TLS, default credentials."""
    return pika.BlockingConnection(
        pika.ConnectionParameters(
            host=RABBITMQ_HOST,
            credentials=pika.PlainCredentials("guest", "guest"),
        )
    )


def get_db():
    """Direct DB access from the gateway — bypasses service layer."""
    return psycopg2.connect(
        host=POSTGRES_HOST,
        database="bankdb",
        user="bankuser",
        password="bankpass",
    )


# --- Helpers ---
def verify_api_key(key: str):
    """Simple set-membership check. No rate limiting, no IP binding."""
    if key not in API_KEYS:
        raise HTTPException(status_code=401, detail="Invalid API key")


# --- Endpoints ---
@app.get("/health")
def health():
    return {"status": "ok", "service": "gateway"}


@app.post("/api/v1/payment")
def create_payment(req: PaymentRequest, x_api_key: str = Header(None)):
    """
    Accept a payment request and queue it for async processing.

    The response returns immediately — the actual debit/credit happens
    asynchronously through the orchestrator and bank service.
    """
    verify_api_key(x_api_key)

    if req.amount <= 0:
        raise HTTPException(400, "Amount must be positive")

    if req.sender_account == req.receiver_account:
        raise HTTPException(400, "Sender and receiver cannot be the same")

    txn_id = str(uuid.uuid4())

    message = {
        "transaction_id": txn_id,
        "sender_account": req.sender_account,
        "receiver_account": req.receiver_account,
        "amount": req.amount,
        "timestamp": time.time(),
    }

    # Publish to the transactions queue — no message signing, no integrity check
    conn = get_rabbit()
    ch = conn.channel()
    ch.queue_declare(queue="transactions", durable=True)
    ch.basic_publish(
        exchange="",
        routing_key="transactions",
        body=json.dumps(message),
        properties=pika.BasicProperties(delivery_mode=2),  # persistent
    )
    conn.close()

    log.info(f"Payment accepted: {txn_id}  {req.sender_account} -> {req.receiver_account}  ₹{req.amount}")

    return {
        "transaction_id": txn_id,
        "status": "ACCEPTED",
        "message": "Payment is being processed asynchronously",
    }


@app.get("/api/v1/balance/{account_id}")
def get_balance(account_id: str, x_api_key: str = Header(None)):
    """Query account balance. Gateway reads DB directly (no service-layer hop)."""
    verify_api_key(x_api_key)

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT balance, bank_code FROM accounts WHERE account_id = %s",
        (account_id,),
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        raise HTTPException(404, "Account not found")

    return {"account_id": account_id, "balance": float(row[0]), "bank_code": row[1]}


@app.get("/api/v1/accounts")
def list_accounts(x_api_key: str = Header(None)):
    """List all accounts with balances. No pagination, no access control per account."""
    verify_api_key(x_api_key)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT account_id, balance, bank_code FROM accounts ORDER BY account_id")
    rows = cur.fetchall()
    conn.close()

    return [
        {"account_id": r[0], "balance": float(r[1]), "bank_code": r[2]} for r in rows
    ]


@app.get("/api/v1/transactions")
def list_transactions(x_api_key: str = Header(None), limit: int = 50):
    """Recent transaction log. No per-user scoping — any valid key sees everything."""
    verify_api_key(x_api_key)

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """SELECT transaction_id, account_id, type, amount, status, processed_at
           FROM transaction_log
           ORDER BY processed_at DESC
           LIMIT %s""",
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()

    return [
        {
            "transaction_id": r[0],
            "account_id": r[1],
            "type": r[2],
            "amount": float(r[3]),
            "status": r[4],
            "processed_at": str(r[5]),
        }
        for r in rows
    ]
