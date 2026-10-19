#!/usr/bin/env python3
"""
Balance Monitor — Check account state via Gateway API and direct DB.
Run before and after each exploit to measure impact.

Usage:
  python3 tools/balance_check.py
  python3 tools/balance_check.py --watch    (polls every 2s)
"""

import sys
import time
import json
import requests
import psycopg2

GATEWAY = "http://gateway:8080"
API_KEY = "test-key-123"

DB_CONFIG = {
    "host": "postgres",
    "database": "bankdb",
    "user": "bankuser",
    "password": "bankpass",
}

LINE = "-" * 62


def get_balances_api():
    """Fetch balances through the gateway API."""
    try:
        r = requests.get(
            f"{GATEWAY}/api/v1/accounts",
            headers={"X-Api-Key": API_KEY},
            timeout=5,
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"  [!] Gateway API error: {e}")
    return None


def get_balances_db():
    """Fetch balances directly from PostgreSQL (bypassing everything)."""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("SELECT account_id, balance, bank_code FROM accounts ORDER BY account_id")
        rows = cur.fetchall()

        cur.execute("SELECT COUNT(*) FROM transaction_log")
        txn_count = cur.fetchone()[0]

        cur.execute("""
            SELECT type, COUNT(*), COALESCE(SUM(amount), 0)
            FROM transaction_log
            GROUP BY type
        """)
        txn_summary = cur.fetchall()

        conn.close()
        return rows, txn_count, txn_summary
    except Exception as e:
        print(f"  [!] Database error: {e}")
        return None, 0, []


def show_balances():
    """Display current state from both sources."""
    print(f"\n{'ACCOUNT STATE':^62}")
    print(LINE)

    # API view
    api_data = get_balances_api()
    if api_data:
        print("  Source: Gateway API")
        total = 0
        for acct in api_data:
            bal = acct["balance"]
            total += bal
            print(f"    {acct['account_id']:12s}  {acct['bank_code']:8s}  ₹{bal:>12,.2f}")
        print(f"    {'TOTAL':12s}  {'':8s}  ₹{total:>12,.2f}")
    print()

    # Direct DB view
    rows, txn_count, txn_summary = get_balances_db()
    if rows:
        print("  Source: Direct database access")
        total = 0
        for r in rows:
            bal = float(r[1])
            total += bal
            print(f"    {r[0]:12s}  {r[2]:8s}  ₹{bal:>12,.2f}")
        print(f"    {'TOTAL':12s}  {'':8s}  ₹{total:>12,.2f}")
        print()
        print(f"  Transaction log: {txn_count} entries")
        for t in txn_summary:
            print(f"    {t[0]:8s}  count={t[1]:4d}  total=₹{float(t[2]):>12,.2f}")

    print(LINE)

    # Conservation check: in a correct system, total money is constant
    if rows:
        expected_total = 10000 + 5000 + 2500 + 8000 + 3000 + 15000  # 43500
        if abs(total - expected_total) > 0.01:
            diff = total - expected_total
            print(f"\n  *** MONEY CONSERVATION VIOLATED ***")
            print(f"  Expected total: ₹{expected_total:,.2f}")
            print(f"  Actual total:   ₹{total:,.2f}")
            print(f"  Difference:     ₹{diff:+,.2f}")
            if diff > 0:
                print(f"  → ₹{diff:,.2f} was CREATED from nothing")
            else:
                print(f"  → ₹{abs(diff):,.2f} was DESTROYED")
        else:
            print(f"\n  Money conservation: OK (total = ₹{total:,.2f})")

    print()


def watch_mode():
    """Poll balances every 2 seconds."""
    print("Watching balances (Ctrl+C to stop)...\n")
    try:
        while True:
            show_balances()
            time.sleep(2)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    if "--watch" in sys.argv:
        watch_mode()
    else:
        show_balances()
