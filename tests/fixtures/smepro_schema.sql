-- Schema of smePro as a fresh install builds it: schema.sql + module tables generated exactly as module.rs create_table does + inventory_batches (v28). schema version 43.
CREATE TABLE _schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE admin_recovery (
    business_id     TEXT PRIMARY KEY REFERENCES businesses(id) ON DELETE CASCADE,
    admin_code_hash TEXT NOT NULL,
    generated_at    TEXT NOT NULL
);
CREATE TABLE audit_log (
    id              TEXT PRIMARY KEY,
    business_id     TEXT NOT NULL,
    user_id         TEXT,
    module_id       TEXT NOT NULL,
    action          TEXT NOT NULL,             -- create/update/delete/export/login/...
    record_id       TEXT,
    details_json    TEXT,                      -- before/after snapshot or extra context
    timestamp       TEXT NOT NULL
);
CREATE TABLE business_settings (
    business_id   TEXT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    key           TEXT NOT NULL,
    value         TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (business_id, key)
);
CREATE TABLE businesses (
    id              TEXT PRIMARY KEY,          -- uuid
    name            TEXT NOT NULL,
    logo_path       TEXT,
    currency        TEXT NOT NULL DEFAULT 'USD',
    tax_rate        REAL NOT NULL DEFAULT 0.0,
    timezone        TEXT NOT NULL DEFAULT 'UTC',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE TABLE currencies (
    id            TEXT PRIMARY KEY,
    business_id   TEXT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    code          TEXT NOT NULL,      -- e.g. "KES", "USD" — or an informal local token, nothing enforces ISO 4217
    symbol        TEXT,
    name          TEXT,
    created_at    TEXT NOT NULL,
    UNIQUE(business_id, code)
);
CREATE TABLE customers (
    id            TEXT PRIMARY KEY,
    business_id   TEXT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    name          TEXT,
    phone         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE(business_id, phone)
);
CREATE TABLE inventory_batches (id TEXT PRIMARY KEY, business_id TEXT NOT NULL, inventory_record_id TEXT NOT NULL, source_po_number TEXT, quantity_received INTEGER NOT NULL, quantity_remaining INTEGER NOT NULL, unit_cost INTEGER NOT NULL, unit_price INTEGER NOT NULL, expiry_date TEXT, received_at TEXT NOT NULL, created_by TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now')), deleted_at TEXT);
CREATE TABLE module_accounting (id TEXT PRIMARY KEY, business_id TEXT NOT NULL, description TEXT NOT NULL, entry_type TEXT NOT NULL, category TEXT, amount INTEGER NOT NULL, transaction_date TEXT, payment_method TEXT, notes TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by TEXT, deleted_at TEXT);
CREATE TABLE module_debt_credit (id TEXT PRIMARY KEY, business_id TEXT NOT NULL, entry_number TEXT NOT NULL, party_name TEXT NOT NULL, direction TEXT NOT NULL, amount INTEGER NOT NULL, due_date TEXT, settled INTEGER, payment_method TEXT, source_order_id TEXT, notes TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by TEXT, deleted_at TEXT, UNIQUE(business_id, entry_number));
CREATE TABLE module_inventory (id TEXT PRIMARY KEY, business_id TEXT NOT NULL, sku TEXT NOT NULL, name TEXT NOT NULL, category TEXT, quantity INTEGER NOT NULL, unit TEXT, unit_cost INTEGER, unit_price INTEGER, currency TEXT, reorder_level INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by TEXT, deleted_at TEXT, UNIQUE(business_id, sku));
CREATE TABLE module_invoice (id TEXT PRIMARY KEY, business_id TEXT NOT NULL, invoice_number TEXT NOT NULL, customer TEXT NOT NULL, customer_email TEXT, customer_phone TEXT, issue_date TEXT NOT NULL, due_date TEXT NOT NULL, status TEXT NOT NULL, items_json TEXT NOT NULL, subtotal INTEGER NOT NULL, tax_rate REAL NOT NULL, tax_amount INTEGER NOT NULL, total INTEGER NOT NULL, notes TEXT, source_sale_id TEXT, sent_at TEXT, paid_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by TEXT, deleted_at TEXT);
CREATE TABLE module_purchasing (id TEXT PRIMARY KEY, business_id TEXT NOT NULL, po_number TEXT NOT NULL, supplier TEXT NOT NULL, item_name TEXT NOT NULL, inventory_record_id TEXT, quantity INTEGER NOT NULL, unit_cost INTEGER NOT NULL, unit_price INTEGER NOT NULL, order_date TEXT, received INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by TEXT, deleted_at TEXT, UNIQUE(business_id, po_number));
CREATE TABLE module_refunds (id TEXT PRIMARY KEY, business_id TEXT NOT NULL, sale_id TEXT NOT NULL, order_id TEXT, item_name TEXT NOT NULL, inventory_record_id TEXT, quantity_refunded INTEGER NOT NULL, refund_amount INTEGER NOT NULL, reason TEXT, restocked INTEGER, customer TEXT, cost_reversed INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by TEXT, deleted_at TEXT);
CREATE TABLE module_sales (id TEXT PRIMARY KEY, business_id TEXT NOT NULL, item_name TEXT NOT NULL, customer TEXT, customer_phone TEXT, branch TEXT, quantity INTEGER NOT NULL, revenue INTEGER NOT NULL, sale_date TEXT, unit_price INTEGER, payment_method TEXT, order_id TEXT, cost_at_sale INTEGER NOT NULL, discount_amount INTEGER NOT NULL, source_batch_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by TEXT, deleted_at TEXT);
CREATE TABLE modules (
    id              TEXT NOT NULL,              -- e.g. "inventory", "sales" — unique PER BUSINESS, not globally
    business_id     TEXT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    display_name    TEXT NOT NULL,
    schema_json     TEXT NOT NULL,             -- raw module definition
    enabled         INTEGER NOT NULL DEFAULT 1,-- 0/1
    table_created   INTEGER NOT NULL DEFAULT 0,-- has its data table been generated?
    created_at      TEXT NOT NULL,
    PRIMARY KEY (business_id, id)
);
CREATE TABLE permissions (
    id              TEXT PRIMARY KEY,
    role_id         TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    module_id       TEXT NOT NULL,
    action          TEXT NOT NULL,
    UNIQUE(role_id, module_id, action)
);
CREATE TABLE roles (
    id              TEXT PRIMARY KEY,
    business_id     TEXT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,             -- e.g. "Owner", "Cashier", "Accountant" — fully user-defined, nothing beyond "Owner" itself is a fixed name anywhere in the engine
    is_system       INTEGER NOT NULL DEFAULT 0,-- system roles can't be deleted (e.g. Owner)
    can_administer  INTEGER NOT NULL DEFAULT 0,-- grants the "admin tier" (settings, reference data) WITHOUT being Owner — a capability flag an Owner toggles per role, not a hardcoded role name like "Manager"
    UNIQUE(business_id, name)
);
CREATE TABLE sessions (
    token           TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    business_id     TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL
);
CREATE TABLE units (
    id            TEXT PRIMARY KEY,
    business_id   TEXT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,      -- e.g. "Kilogram", "Dozen", "Crate of 24"
    abbreviation  TEXT,               -- e.g. "kg", "dz"
    created_at    TEXT NOT NULL,
    UNIQUE(business_id, name)
);
CREATE TABLE users (
    id              TEXT PRIMARY KEY,
    business_id     TEXT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    username        TEXT NOT NULL,
    password_hash   TEXT NOT NULL,
    security_q1     TEXT,
    security_a1_hash TEXT,
    security_q2     TEXT,
    security_a2_hash TEXT,
    role_id         TEXT NOT NULL,
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    UNIQUE(business_id, username)
);
CREATE INDEX idx_audit_business_time ON audit_log(business_id, timestamp);
CREATE UNIQUE INDEX idx_customers_name_only ON customers(business_id, name) WHERE phone IS NULL;
CREATE INDEX idx_permissions_role ON permissions(role_id);
