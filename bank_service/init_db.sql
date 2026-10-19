-- ============================================================
--  AsyncPay Bank Database — Schema & Seed Data
-- ============================================================

-- Account ledger
CREATE TABLE IF NOT EXISTS accounts (
    account_id   VARCHAR(50)    PRIMARY KEY,
    balance      DECIMAL(15, 2) NOT NULL DEFAULT 0.00,
    bank_code    VARCHAR(10)    NOT NULL,
    created_at   TIMESTAMP      DEFAULT CURRENT_TIMESTAMP
);

-- Transaction log (append-only audit trail)
CREATE TABLE IF NOT EXISTS transaction_log (
    id              SERIAL         PRIMARY KEY,
    transaction_id  VARCHAR(100)   NOT NULL,
    account_id      VARCHAR(50)    NOT NULL,
    type            VARCHAR(10)    NOT NULL,   -- DEBIT or CREDIT
    amount          DECIMAL(15, 2) NOT NULL,
    status          VARCHAR(20)    NOT NULL,
    processed_at    TIMESTAMP      DEFAULT CURRENT_TIMESTAMP
);

-- Note: No unique constraint on (transaction_id, type) in transaction_log
-- This means the same transaction_id can be logged multiple times

-- ── Seed Accounts ─────────────────────────────────────────
-- Two simulated banks with test accounts

-- Bank A accounts
INSERT INTO accounts (account_id, balance, bank_code) VALUES
    ('ACCT-1001', 10000.00, 'BANK-A'),
    ('ACCT-1002',  5000.00, 'BANK-A'),
    ('ACCT-1003',  2500.00, 'BANK-A')
ON CONFLICT (account_id) DO NOTHING;

-- Bank B accounts
INSERT INTO accounts (account_id, balance, bank_code) VALUES
    ('ACCT-2001',  8000.00, 'BANK-B'),
    ('ACCT-2002',  3000.00, 'BANK-B'),
    ('ACCT-2003', 15000.00, 'BANK-B')
ON CONFLICT (account_id) DO NOTHING;
