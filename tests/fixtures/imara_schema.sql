-- Schema of Imara v2 as created by its own code (backend/db initDb + migrations), snapshot imara-v2-main (migrations up to 2.14.0).
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,
    table_name TEXT,
    record_id INTEGER,
    old_values TEXT,
    new_values TEXT,
    ip_address TEXT,
    logged_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
  , user_name TEXT DEFAULT NULL);
CREATE TABLE config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_by INTEGER REFERENCES users(id),
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
  );
CREATE TABLE invoice_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    piece_type_id INTEGER REFERENCES piece_types(id),
    description TEXT NOT NULL,
    gauge TEXT DEFAULT '',
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    unit_price REAL NOT NULL CHECK(unit_price >= 0),
    line_total REAL NOT NULL CHECK(line_total >= 0)
  );
CREATE TABLE invoice_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    payment_date TEXT NOT NULL,
    amount REAL NOT NULL CHECK(amount > 0),
    payment_method TEXT NOT NULL DEFAULT 'cash',
    notes TEXT DEFAULT '',
    recorded_by INTEGER NOT NULL REFERENCES users(id),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
  , recorded_by_name TEXT DEFAULT NULL);
CREATE TABLE invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number TEXT NOT NULL UNIQUE,
    invoice_date TEXT NOT NULL,
    due_date TEXT DEFAULT '',
    customer_name TEXT NOT NULL,
    customer_phone TEXT DEFAULT '',
    customer_email TEXT DEFAULT '',
    customer_address TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'partial_payment' CHECK(status IN ('partial_payment','paid','cancelled')),
    subtotal REAL NOT NULL DEFAULT 0,
    discount_pct REAL NOT NULL DEFAULT 0,
    discount_amount REAL NOT NULL DEFAULT 0,
    tax_pct REAL NOT NULL DEFAULT 0,
    tax_amount REAL NOT NULL DEFAULT 0,
    total_amount REAL NOT NULL DEFAULT 0,
    amount_paid REAL NOT NULL DEFAULT 0,
    notes TEXT DEFAULT '',
    sale_id INTEGER,
    reversal_source TEXT DEFAULT NULL,
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
  , backup_at DATETIME DEFAULT NULL, created_by_name TEXT DEFAULT NULL);
CREATE TABLE no_activity_days (
    entry_date TEXT PRIMARY KEY,
    confirmed_by INTEGER NOT NULL REFERENCES users(id),
    confirmed_by_name TEXT,
    notes TEXT DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
  );
CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    role_target TEXT,
    type TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL,
    read INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
  );
CREATE TABLE order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    piece_type_id INTEGER NOT NULL REFERENCES piece_types(id),
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    selling_price REAL,
    gauge_source TEXT DEFAULT '',
    transport_to_market REAL,
    sale_id INTEGER REFERENCES sales(id) ON DELETE SET NULL
  );
CREATE TABLE orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_date TEXT NOT NULL,
    buyer_name TEXT NOT NULL DEFAULT 'Walk-in Customer',
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'converted', 'cancelled')),
    notes TEXT DEFAULT '',
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    converted_at DATETIME,
    invoice_id INTEGER REFERENCES invoices(id)
  , created_by_name TEXT DEFAULT NULL);
CREATE TABLE password_reset_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
  );
CREATE TABLE "payments" (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              payment_date TEXT NOT NULL,
              category TEXT NOT NULL CHECK(category IN (
                'wages_operator','wages_knuckler','rent','supplier',
                'sack','transport_to_market','other'
              )),
              payee_user_id INTEGER REFERENCES users(id),
              payee_supplier_id INTEGER REFERENCES suppliers(id),
              payee_name TEXT,
              rent_month TEXT DEFAULT NULL,
              amount REAL NOT NULL CHECK(amount > 0),
              notes TEXT DEFAULT '',
              recorded_by INTEGER NOT NULL REFERENCES users(id),
              created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            , recorded_by_name TEXT DEFAULT NULL);
CREATE TABLE piece_types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    length_m REAL NOT NULL CHECK(length_m > 0),
    weight_kg REAL NOT NULL CHECK(weight_kg > 0),
    default_price REAL NOT NULL DEFAULT 0 CHECK(default_price >= 0),
    active INTEGER NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
  );
CREATE TABLE production (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_date TEXT NOT NULL,
    kgs_used REAL NOT NULL CHECK(kgs_used >= 0),
    gauge TEXT DEFAULT '',
    purchase_id INTEGER REFERENCES purchases(id),
    operator_id INTEGER REFERENCES users(id),
    knuckler_id INTEGER REFERENCES users(id),
    operator_cost REAL NOT NULL DEFAULT 0,
    knuckler_cost REAL NOT NULL DEFAULT 0,
    sack_cost REAL NOT NULL DEFAULT 0,
    rent_allocation REAL NOT NULL DEFAULT 0,
    total_cost REAL NOT NULL DEFAULT 0,
    entered_by INTEGER NOT NULL REFERENCES users(id),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
  , entered_by_name TEXT DEFAULT NULL, operator_name TEXT DEFAULT NULL, knuckler_name TEXT DEFAULT NULL);
CREATE TABLE production_batch_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    production_id INTEGER NOT NULL REFERENCES production(id) ON DELETE CASCADE,
    purchase_id INTEGER NOT NULL REFERENCES purchases(id),
    kgs_drawn REAL NOT NULL CHECK(kgs_drawn > 0),
    landed_cost_per_kg REAL NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
  );
CREATE TABLE production_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    production_id INTEGER NOT NULL REFERENCES production(id) ON DELETE CASCADE,
    piece_type_id INTEGER NOT NULL REFERENCES piece_types(id),
    pieces_produced INTEGER NOT NULL CHECK(pieces_produced >= 0)
  );
CREATE TABLE purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_date TEXT NOT NULL,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    kgs_bought REAL NOT NULL CHECK(kgs_bought >= 0),
    cost_per_kg REAL NOT NULL CHECK(cost_per_kg >= 0),
    transport_cost REAL NOT NULL DEFAULT 0 CHECK(transport_cost >= 0),
    gauge TEXT DEFAULT '',
    batch_name TEXT NOT NULL DEFAULT '',
    kgs_remaining REAL,
    entered_by INTEGER NOT NULL REFERENCES users(id),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
  , entered_by_name TEXT DEFAULT NULL, supplier_name TEXT DEFAULT NULL);
CREATE TABLE rent_months (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    month TEXT NOT NULL UNIQUE,
    amount_due REAL NOT NULL DEFAULT 0,
    paid INTEGER NOT NULL DEFAULT 0 CHECK(paid IN (0,1)),
    payment_id INTEGER REFERENCES payments(id),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
  );
CREATE TABLE sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_date TEXT NOT NULL,
    piece_type_id INTEGER NOT NULL REFERENCES piece_types(id),
    quantity INTEGER NOT NULL CHECK(quantity >= 0),
    selling_price REAL NOT NULL CHECK(selling_price >= 0),
    default_price REAL NOT NULL DEFAULT 0,
    price_overridden INTEGER NOT NULL DEFAULT 0,
    transport_to_market REAL NOT NULL DEFAULT 0,
    buyer_name TEXT DEFAULT '',
    gauge_source TEXT DEFAULT '',
    entered_by INTEGER NOT NULL REFERENCES users(id),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
  , wire_cost_per_kg REAL NOT NULL DEFAULT 0, entered_by_name TEXT DEFAULT NULL, conversion_cost_per_piece REAL NOT NULL DEFAULT 0, wire_cost_per_piece REAL NOT NULL DEFAULT 0);
CREATE TABLE schema_migrations (
        id TEXT PRIMARY KEY,
        applied_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        version TEXT
      );
CREATE TABLE security_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    q1 TEXT NOT NULL,
    a1_hash TEXT NOT NULL,
    q2 TEXT NOT NULL,
    a2_hash TEXT NOT NULL,
    q3 TEXT NOT NULL,
    a3_hash TEXT NOT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
  );
CREATE TABLE suppliers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
  );
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('owner','admin','knuckler','operator')),
    full_name TEXT NOT NULL,
    phone TEXT DEFAULT '',
    email TEXT DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    password_changed_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
  );
CREATE INDEX idx_audit_user        ON audit_log(user_id);
CREATE INDEX idx_invoice_items_invoice ON invoice_items(invoice_id);
CREATE INDEX idx_invoice_payments_date    ON invoice_payments(payment_date);
CREATE INDEX idx_invoice_payments_invoice ON invoice_payments(invoice_id);
CREATE INDEX idx_invoices_customer ON invoices(customer_name);
CREATE INDEX idx_invoices_sale_id         ON invoices(sale_id);
CREATE INDEX idx_invoices_status   ON invoices(status);
CREATE INDEX idx_notifications_role    ON notifications(role_target, read);
CREATE INDEX idx_notifications_user    ON notifications(user_id, read);
CREATE INDEX idx_order_items_order_id  ON order_items(order_id);
CREATE INDEX idx_payments_category ON payments(category);
CREATE INDEX idx_payments_date     ON payments(payment_date);
CREATE INDEX idx_pbu_production ON production_batch_usage(production_id);
CREATE INDEX idx_pbu_purchase   ON production_batch_usage(purchase_id);
CREATE INDEX idx_production_date   ON production(entry_date);
CREATE INDEX idx_production_gauge  ON production(gauge);
CREATE INDEX idx_production_items_prod_id ON production_items(production_id);
CREATE INDEX idx_purchases_date    ON purchases(entry_date);
CREATE INDEX idx_purchases_gauge   ON purchases(gauge);
CREATE INDEX idx_sales_date        ON sales(entry_date);
CREATE INDEX idx_sales_gauge       ON sales(gauge_source);
CREATE INDEX idx_sales_piece_type         ON sales(piece_type_id);
