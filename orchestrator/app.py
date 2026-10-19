"""
AsyncPay Orchestrator — Saga coordinator for payment transactions.

Listens on the 'transactions' queue for new payment requests.
Implements a two-phase flow:
  1. Forward to 'debit_requests'  →  Bank debits sender
  2. On DEBIT_COMPLETED, forward to 'credit_requests'  →  Bank credits receiver

Transaction state is held in an in-memory dict (lost on restart — no persistence).
No message signing or integrity verification at any stage.
"""

import os
import json
import time
import logging

import pika

logging.basicConfig(level=logging.INFO, format="[ORCHESTRATOR] %(asctime)s  %(message)s")
log = logging.getLogger(__name__)

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")

# In-memory transaction store — no persistence, no replication
# If this process restarts mid-transaction, the saga is orphaned
transactions = {}

QUEUES = [
    "transactions",
    "debit_requests",
    "credit_requests",
    "transaction_results",
]


def connect_rabbit():
    """Retry loop for RabbitMQ connection. Plain AMQP, no TLS."""
    for attempt in range(30):
        try:
            conn = pika.BlockingConnection(
                pika.ConnectionParameters(
                    host=RABBITMQ_HOST,
                    credentials=pika.PlainCredentials("guest", "guest"),
                    heartbeat=600,
                    blocked_connection_timeout=300,
                )
            )
            log.info("Connected to RabbitMQ")
            return conn
        except pika.exceptions.AMQPConnectionError:
            log.info(f"Waiting for RabbitMQ... attempt {attempt + 1}/30")
            time.sleep(2)
    raise RuntimeError("Could not connect to RabbitMQ after 30 attempts")


def publish(channel, queue, message):
    """Publish a message. No signing, no encryption, no schema validation."""
    channel.basic_publish(
        exchange="",
        routing_key=queue,
        body=json.dumps(message),
        properties=pika.BasicProperties(delivery_mode=2),
    )


def on_new_transaction(ch, method, properties, body):
    """
    Handle incoming payment request from the gateway.
    Forwards directly to the debit queue without re-validating
    the message contents or verifying its origin.
    """
    try:
        msg = json.loads(body)
    except json.JSONDecodeError:
        log.error(f"Malformed message on transactions queue: {body[:200]}")
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return

    txn_id = msg.get("transaction_id", "UNKNOWN")

    log.info(
        f"NEW  {txn_id}  "
        f"{msg.get('sender_account')} → {msg.get('receiver_account')}  "
        f"₹{msg.get('amount')}"
    )

    # Store full transaction context in memory
    transactions[txn_id] = {**msg, "status": "DEBIT_PENDING"}

    # Forward to bank for debit — passes the message through as-is
    publish(ch, "debit_requests", msg)

    ch.basic_ack(delivery_tag=method.delivery_tag)


def on_transaction_result(ch, method, properties, body):
    """
    Handle results from the bank service.
    On DEBIT_COMPLETED → trigger credit.
    On CREDIT_COMPLETED → mark done.
    On failure → mark failed (no compensating transaction).
    """
    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        log.error(f"Malformed result message: {body[:200]}")
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return

    txn_id = result.get("transaction_id", "UNKNOWN")
    status = result.get("status", "UNKNOWN")

    log.info(f"RESULT  {txn_id}  status={status}")

    if status == "DEBIT_COMPLETED":
        # Retrieve original transaction data for the credit phase
        txn = transactions.get(txn_id, result)

        credit_msg = {
            "transaction_id": txn_id,
            "receiver_account": txn.get("receiver_account"),
            "amount": txn.get("amount"),
            "sender_account": txn.get("sender_account"),
            "timestamp": time.time(),
        }
        publish(ch, "credit_requests", credit_msg)

        if txn_id in transactions:
            transactions[txn_id]["status"] = "CREDIT_PENDING"

        log.info(f"CREDIT triggered for {txn_id}")

    elif status == "CREDIT_COMPLETED":
        if txn_id in transactions:
            transactions[txn_id]["status"] = "COMPLETED"
        log.info(f"COMPLETED  {txn_id}")

    elif status == "DEBIT_FAILED":
        if txn_id in transactions:
            transactions[txn_id]["status"] = "FAILED"
        log.info(f"FAILED  {txn_id}  reason={result.get('detail')}")

    elif status == "CREDIT_FAILED":
        # Debit succeeded but credit failed — money is in limbo
        # No compensating rollback implemented
        if txn_id in transactions:
            transactions[txn_id]["status"] = "PARTIAL_FAILURE"
        log.warning(
            f"PARTIAL FAILURE  {txn_id}  — debit went through but credit failed. "
            f"No automatic rollback."
        )

    ch.basic_ack(delivery_tag=method.delivery_tag)


def main():
    connection = connect_rabbit()
    channel = connection.channel()

    # Declare all queues up front
    for q in QUEUES:
        channel.queue_declare(queue=q, durable=True)

    channel.basic_qos(prefetch_count=1)

    channel.basic_consume(queue="transactions", on_message_callback=on_new_transaction)
    channel.basic_consume(
        queue="transaction_results", on_message_callback=on_transaction_result
    )

    log.info("Orchestrator running. Listening on: transactions, transaction_results")

    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        log.info("Shutting down...")
        channel.stop_consuming()
    finally:
        connection.close()


if __name__ == "__main__":
    main()
