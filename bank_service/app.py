"""
AsyncPay Bank Service — Processes debit and credit operations.

Consumes from 'debit_requests' and 'credit_requests' queues.
Publishes results to 'transaction_results' queue.

Account balances are stored in PostgreSQL.

This service has several intentional design weaknesses that
reflect real-world vulnerabilities found in financial systems.
"""

import os
import json
import time
import logging

import pika
import psycopg2

logging.basicConfig(level=logging.INFO, format="[BANK-SVC] %(asctime)s  %(message)s")
log = logging.getLogger(__name__)

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres")


def connect_db():
    """Connect to PostgreSQL with retry. Credentials in plaintext env vars."""
    for attempt in range(30):
        try:
            conn = psycopg2.connect(
                host=POSTGRES_HOST,
                database="bankdb",
                user="bankuser",
                password="bankpass",
            )
            return conn
        except psycopg2.OperationalError:
            log.info(f"Waiting for PostgreSQL... attempt {attempt + 1}/30")
            time.sleep(2)
    raise RuntimeError("Could not connect to PostgreSQL")


def connect_rabbit():
    """Connect to RabbitMQ with retry. Plain AMQP, no TLS."""
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
    raise RuntimeError("Could not connect to RabbitMQ")


def publish_result(channel, txn_id, status, detail, original_msg=None):
    """Publish a transaction result. Message is unsigned and unencrypted."""
    result = {
        "transaction_id": txn_id,
        "status": status,
        "detail": detail,
        "timestamp": time.time(),
    }
    # Pass through original transaction data so the orchestrator
    # has context for the next phase
    if original_msg:
        result["sender_account"] = original_msg.get("sender_account")
        result["receiver_account"] = original_msg.get("receiver_account")
        result["amount"] = original_msg.get("amount")

    channel.basic_publish(
        exchange="",
        routing_key="transaction_results",
        body=json.dumps(result),
        properties=pika.BasicProperties(delivery_mode=2),
    )


def handle_debit(ch, method, properties, body):
    """
    Process a debit request.

    The balance check and balance update are TWO SEPARATE STEPS
    with a processing delay between them. This is the textbook
    TOCTOU (Time-of-Check-to-Time-of-Use) pattern.
    """
    try:
        msg = json.loads(body)
    except json.JSONDecodeError:
        log.error(f"Malformed debit message: {body[:200]}")
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return

    txn_id = msg.get("transaction_id", "UNKNOWN")
    account = msg.get("sender_account")
    amount = msg.get("amount", 0)

    log.info(f"DEBIT request: {txn_id}  account={account}  amount=₹{amount}")

    conn = connect_db()
    cur = conn.cursor()

    try:
        # ── Step 1: Read the current balance ──
        cur.execute(
            "SELECT balance FROM accounts WHERE account_id = %s", (account,)
        )
        row = cur.fetchone()

        if not row:
            publish_result(ch, txn_id, "DEBIT_FAILED", "Account not found", msg)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            conn.close()
            return

        balance = float(row[0])

        # ── Simulated processing delay ──
        # Represents fraud checks, compliance validation, etc.
        # This widens the window between check and update.
        time.sleep(0.08)

        # ── Step 2: Debit if sufficient ──
        # Uses the STALE balance read in Step 1.
        # Another instance may have debited between Step 1 and Step 2.
        if balance >= amount:
            new_balance = balance - amount
            cur.execute(
                "UPDATE accounts SET balance = %s WHERE account_id = %s",
                (new_balance, account),
            )
            cur.execute(
                """INSERT INTO transaction_log
                   (transaction_id, account_id, type, amount, status)
                   VALUES (%s, %s, 'DEBIT', %s, 'COMPLETED')""",
                (txn_id, account, amount),
            )
            conn.commit()

            log.info(f"DEBIT OK: {account}  ₹{balance} → ₹{new_balance}")
            publish_result(
                ch, txn_id, "DEBIT_COMPLETED",
                f"Debited {amount} from {account}", msg,
            )
        else:
            log.info(f"DEBIT FAIL: {account}  balance=₹{balance} < ₹{amount}")
            publish_result(ch, txn_id, "DEBIT_FAILED", "Insufficient balance", msg)

    except Exception as e:
        log.error(f"Error processing debit: {e}")
        conn.rollback()
        publish_result(ch, txn_id, "DEBIT_FAILED", f"Internal error: {e}", msg)

    finally:
        conn.close()

    ch.basic_ack(delivery_tag=method.delivery_tag)


def handle_credit(ch, method, properties, body):
    """
    Process a credit request.

    Credits the receiver account. Does not verify that a corresponding
    debit actually occurred — it trusts the message on the queue.
    """
    try:
        msg = json.loads(body)
    except json.JSONDecodeError:
        log.error(f"Malformed credit message: {body[:200]}")
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return

    txn_id = msg.get("transaction_id", "UNKNOWN")
    account = msg.get("receiver_account")
    amount = msg.get("amount", 0)

    log.info(f"CREDIT request: {txn_id}  account={account}  amount=₹{amount}")

    conn = connect_db()
    cur = conn.cursor()

    try:
        # No idempotency check — if this message is replayed,
        # the account gets credited again
        cur.execute(
            "UPDATE accounts SET balance = balance + %s WHERE account_id = %s",
            (amount, account),
        )
        cur.execute(
            """INSERT INTO transaction_log
               (transaction_id, account_id, type, amount, status)
               VALUES (%s, %s, 'CREDIT', %s, 'COMPLETED')""",
            (txn_id, account, amount),
        )
        conn.commit()

        log.info(f"CREDIT OK: {account}  +₹{amount}")
        publish_result(
            ch, txn_id, "CREDIT_COMPLETED",
            f"Credited {amount} to {account}", msg,
        )

    except Exception as e:
        log.error(f"Error processing credit: {e}")
        conn.rollback()
        publish_result(ch, txn_id, "CREDIT_FAILED", f"Internal error: {e}", msg)

    finally:
        conn.close()

    ch.basic_ack(delivery_tag=method.delivery_tag)


def main():
    rabbit_conn = connect_rabbit()
    channel = rabbit_conn.channel()

    channel.queue_declare(queue="debit_requests", durable=True)
    channel.queue_declare(queue="credit_requests", durable=True)
    channel.queue_declare(queue="transaction_results", durable=True)

    # prefetch_count=1 → process one message at a time per instance
    # But multiple instances (via --scale) process in parallel
    channel.basic_qos(prefetch_count=1)

    channel.basic_consume(queue="debit_requests", on_message_callback=handle_debit)
    channel.basic_consume(queue="credit_requests", on_message_callback=handle_credit)

    log.info("Bank Service running. Listening on: debit_requests, credit_requests")

    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        log.info("Shutting down...")
        channel.stop_consuming()
    finally:
        rabbit_conn.close()


if __name__ == "__main__":
    main()
