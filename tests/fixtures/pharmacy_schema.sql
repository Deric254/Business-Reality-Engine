-- Schema of Pharmacy ERP as created by its own alembic migrations, head 0043_purchase_restock_snapshot.
CREATE TABLE ai_conversation_messages (
	id INTEGER NOT NULL, 
	conversation_id INTEGER NOT NULL, 
	prompt TEXT NOT NULL, 
	answer TEXT NOT NULL, 
	provider_used VARCHAR(20), 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(conversation_id) REFERENCES ai_conversations (id)
);
CREATE TABLE ai_conversations (
	id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	title VARCHAR(120) NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE TABLE ai_provider_keys (
	id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	provider VARCHAR(8) NOT NULL, 
	encrypted_key VARCHAR(1000) NOT NULL, 
	key_hint VARCHAR(4) NOT NULL, 
	priority INTEGER DEFAULT '1' NOT NULL, 
	is_active BOOLEAN DEFAULT 1 NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP, 
	last_used_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE TABLE alembic_version (
	version_num VARCHAR(32) NOT NULL, 
	CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);
CREATE TABLE audit_logs (
	id INTEGER NOT NULL, 
	user_id INTEGER, 
	action VARCHAR(100) NOT NULL, 
	entity_type VARCHAR(50) NOT NULL, 
	entity_id VARCHAR(50) NOT NULL, 
	old_value TEXT, 
	new_value TEXT, 
	ip_address VARCHAR(45), 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP, user_name_snapshot VARCHAR(120), 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE TABLE "backup_logs" (
	id INTEGER NOT NULL, 
	status VARCHAR(7) NOT NULL, 
	provider VARCHAR(12) NOT NULL, 
	reference VARCHAR(255), 
	manifest_json TEXT, 
	size_bytes INTEGER, 
	error_message VARCHAR(500), 
	created_by_user_id INTEGER NOT NULL, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP), 
	restored_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(created_by_user_id) REFERENCES users (id)
);
CREATE TABLE "backup_oauth_tokens" (
	id INTEGER NOT NULL, 
	provider VARCHAR(12) NOT NULL, 
	encrypted_refresh_token BLOB NOT NULL, 
	connected_by_user_id INTEGER NOT NULL, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP), 
	PRIMARY KEY (id), 
	UNIQUE (provider), 
	FOREIGN KEY(connected_by_user_id) REFERENCES users (id)
);
CREATE TABLE "business_config" (
	id INTEGER NOT NULL, 
	business_name VARCHAR(120) DEFAULT 'My Pharmacy' NOT NULL, 
	slogan VARCHAR(255) DEFAULT ('') NOT NULL, 
	logo_url TEXT, 
	primary_color VARCHAR(7) DEFAULT '#0EA5E9' NOT NULL, 
	secondary_color VARCHAR(7) DEFAULT '#0F172A' NOT NULL, 
	receipt_header_text VARCHAR(255) DEFAULT ('') NOT NULL, 
	receipt_footer_text VARCHAR(255) DEFAULT 'Thank you for your purchase' NOT NULL, 
	currency VARCHAR(3) DEFAULT 'KES' NOT NULL, 
	tax_rate FLOAT DEFAULT '0' NOT NULL, 
	tax_id VARCHAR(50), 
	contact_phone VARCHAR(30), 
	contact_email VARCHAR(120), 
	address VARCHAR(255), 
	default_language VARCHAR(10) DEFAULT 'en' NOT NULL, 
	timezone VARCHAR(50) DEFAULT 'Africa/Nairobi' NOT NULL, 
	low_stock_threshold_default INTEGER DEFAULT '10' NOT NULL, 
	expiry_alert_days VARCHAR(50) DEFAULT '90,60,30' NOT NULL, 
	updated_at DATETIME DEFAULT (CURRENT_TIMESTAMP), 
	loyalty_program_enabled BOOLEAN DEFAULT 0 NOT NULL, 
	loyalty_points_per_currency_unit FLOAT DEFAULT '1' NOT NULL, 
	theme_name VARCHAR(30) DEFAULT 'ledger' NOT NULL, local_backup_dir_override VARCHAR(500), 
	PRIMARY KEY (id)
);
CREATE TABLE categories (
	id INTEGER NOT NULL, 
	name VARCHAR(80) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);
CREATE TABLE customers (
	id INTEGER NOT NULL, 
	name VARCHAR(150) NOT NULL, 
	phone VARCHAR(30), 
	email VARCHAR(120), 
	loyalty_points INTEGER DEFAULT '0' NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP, 
	PRIMARY KEY (id)
);
CREATE TABLE "medicine_batches" (
	id INTEGER NOT NULL, 
	product_id INTEGER NOT NULL, 
	batch_number VARCHAR(80) NOT NULL, 
	expiry_date DATE NOT NULL, 
	qty_received INTEGER NOT NULL, 
	qty_remaining INTEGER NOT NULL, 
	cost_price INTEGER DEFAULT '0' NOT NULL, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP), 
	locked_by_stock_take_id INTEGER, 
	selling_price INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_medicine_batches_locked_by_stock_take_id FOREIGN KEY(locked_by_stock_take_id) REFERENCES stock_takes (id), 
	CONSTRAINT ck_medicine_batches_qty_remaining_nonneg CHECK (qty_remaining >= 0), 
	CONSTRAINT ck_medicine_batches_qty_received_nonneg CHECK (qty_received >= 0), 
	CONSTRAINT ck_medicine_batches_prices_nonneg CHECK (cost_price >= 0 AND selling_price >= 0), 
	FOREIGN KEY(product_id) REFERENCES products (id)
);
CREATE TABLE "payments" (
	id INTEGER NOT NULL, 
	sale_id INTEGER NOT NULL, 
	method VARCHAR(5) NOT NULL, 
	amount INTEGER NOT NULL, 
	reference VARCHAR(100), 
	PRIMARY KEY (id), 
	CONSTRAINT ck_payments_amount_positive CHECK (amount > 0), 
	FOREIGN KEY(sale_id) REFERENCES sales (id)
);
CREATE TABLE permissions (
	id INTEGER NOT NULL, 
	code VARCHAR(100) NOT NULL, 
	description VARCHAR(255) NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE "products" (
	id INTEGER NOT NULL, 
	name VARCHAR(150) NOT NULL, 
	barcode VARCHAR(64), 
	unit VARCHAR(30) DEFAULT 'unit' NOT NULL, 
	category_id INTEGER, 
	reorder_point INTEGER DEFAULT '10' NOT NULL, 
	default_selling_price INTEGER DEFAULT '0' NOT NULL, 
	is_active BOOLEAN DEFAULT 1 NOT NULL, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP), 
	deleted_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(category_id) REFERENCES categories (id)
);
CREATE TABLE "purchase_order_items" (
	id INTEGER NOT NULL, 
	purchase_order_id INTEGER NOT NULL, 
	product_id INTEGER NOT NULL, 
	quantity_ordered INTEGER NOT NULL, 
	unit_cost_expected INTEGER NOT NULL, 
	quantity_received INTEGER, 
	unit_cost_actual INTEGER, 
	batch_id INTEGER, batch_cost_before INTEGER, batch_cost_after INTEGER, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_purchase_order_items_ordered_positive CHECK (quantity_ordered > 0), 
	CONSTRAINT ck_purchase_order_items_received_nonneg CHECK (quantity_received >= 0), 
	CONSTRAINT ck_purchase_order_items_costs_nonneg CHECK (unit_cost_expected >= 0 AND unit_cost_actual >= 0), 
	FOREIGN KEY(purchase_order_id) REFERENCES purchase_orders (id), 
	FOREIGN KEY(batch_id) REFERENCES medicine_batches (id), 
	FOREIGN KEY(product_id) REFERENCES products (id)
);
CREATE TABLE "purchase_orders" (
	id INTEGER NOT NULL, 
	supplier_id INTEGER NOT NULL, 
	status VARCHAR(8) DEFAULT 'RECEIVED' NOT NULL, 
	created_by_user_id INTEGER NOT NULL, 
	notes VARCHAR(255), 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP), 
	received_at DATETIME, reversed_at DATETIME, reversal_reason VARCHAR(255), 
	PRIMARY KEY (id), 
	FOREIGN KEY(created_by_user_id) REFERENCES users (id), 
	FOREIGN KEY(supplier_id) REFERENCES suppliers (id)
);
CREATE TABLE "refund_items" (
	id INTEGER NOT NULL, 
	refund_id INTEGER NOT NULL, 
	sale_item_id INTEGER NOT NULL, 
	product_id INTEGER NOT NULL, 
	batch_id INTEGER NOT NULL, 
	quantity INTEGER NOT NULL, 
	unit_price INTEGER NOT NULL, 
	line_total INTEGER NOT NULL, 
	restocked BOOLEAN DEFAULT 1 NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_refund_items_quantity_positive CHECK (quantity > 0), 
	CONSTRAINT ck_refund_items_amounts_nonneg CHECK (unit_price >= 0 AND line_total >= 0), 
	FOREIGN KEY(sale_item_id) REFERENCES sale_items (id), 
	FOREIGN KEY(product_id) REFERENCES products (id), 
	FOREIGN KEY(batch_id) REFERENCES medicine_batches (id), 
	FOREIGN KEY(refund_id) REFERENCES refunds (id)
);
CREATE TABLE "refunds" (
	id INTEGER NOT NULL, 
	sale_id INTEGER NOT NULL, 
	processed_by_user_id INTEGER NOT NULL, 
	reason VARCHAR(15) NOT NULL, 
	notes VARCHAR(255), 
	method VARCHAR(5) NOT NULL, 
	total_amount INTEGER NOT NULL, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP), 
	PRIMARY KEY (id), 
	CONSTRAINT ck_refunds_total_nonneg CHECK (total_amount >= 0), 
	FOREIGN KEY(processed_by_user_id) REFERENCES users (id), 
	FOREIGN KEY(sale_id) REFERENCES sales (id)
);
CREATE TABLE role_permissions (
	role_id INTEGER NOT NULL, 
	permission_id INTEGER NOT NULL, 
	PRIMARY KEY (role_id, permission_id), 
	FOREIGN KEY(role_id) REFERENCES roles (id), 
	FOREIGN KEY(permission_id) REFERENCES permissions (id)
);
CREATE TABLE "roles" (
	id INTEGER NOT NULL, 
	name VARCHAR(50) NOT NULL, 
	description VARCHAR(255) DEFAULT (''), 
	is_system BOOLEAN DEFAULT 0 NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);
CREATE TABLE "sale_items" (
	id INTEGER NOT NULL, 
	sale_id INTEGER NOT NULL, 
	product_id INTEGER NOT NULL, 
	batch_id INTEGER NOT NULL, 
	quantity INTEGER NOT NULL, 
	unit_price INTEGER NOT NULL, 
	line_total INTEGER NOT NULL, 
	unit_cost INTEGER NOT NULL, 
	qty_refunded INTEGER DEFAULT '0' NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_sale_items_quantity_positive CHECK (quantity > 0), 
	CONSTRAINT ck_sale_items_qty_refunded_in_range CHECK (qty_refunded >= 0 AND qty_refunded <= quantity), 
	CONSTRAINT ck_sale_items_amounts_nonneg CHECK (unit_price >= 0 AND unit_cost >= 0 AND line_total >= 0), 
	FOREIGN KEY(sale_id) REFERENCES sales (id), 
	FOREIGN KEY(product_id) REFERENCES products (id), 
	FOREIGN KEY(batch_id) REFERENCES medicine_batches (id)
);
CREATE TABLE "sales" (
	id INTEGER NOT NULL, 
	cashier_user_id INTEGER NOT NULL, 
	subtotal INTEGER NOT NULL, 
	discount_amount INTEGER DEFAULT '0' NOT NULL, 
	total_amount INTEGER NOT NULL, 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP), 
	customer_id INTEGER, 
	idempotency_key VARCHAR(64), 
	PRIMARY KEY (id), 
	CONSTRAINT fk_sales_customer_id FOREIGN KEY(customer_id) REFERENCES customers (id), 
	CONSTRAINT ck_sales_amounts_nonneg CHECK (subtotal >= 0 AND discount_amount >= 0 AND total_amount >= 0), 
	FOREIGN KEY(cashier_user_id) REFERENCES users (id)
);
CREATE TABLE setup_lock (
	id INTEGER NOT NULL, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, 
	PRIMARY KEY (id)
);
CREATE TABLE stock_movements (
	id INTEGER NOT NULL, 
	batch_id INTEGER NOT NULL, 
	movement_type VARCHAR(10) NOT NULL, 
	quantity_delta INTEGER NOT NULL, 
	reason VARCHAR(255), 
	reference VARCHAR(100), 
	created_by_user_id INTEGER, 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP, 
	PRIMARY KEY (id), 
	FOREIGN KEY(batch_id) REFERENCES medicine_batches (id), 
	FOREIGN KEY(created_by_user_id) REFERENCES users (id)
);
CREATE TABLE "stock_take_items" (
	id INTEGER NOT NULL, 
	stock_take_id INTEGER NOT NULL, 
	batch_id INTEGER NOT NULL, 
	product_id INTEGER NOT NULL, 
	expected_qty INTEGER NOT NULL, 
	physical_qty INTEGER, 
	reason VARCHAR(255), 
	counted_by_user_id INTEGER, 
	counted_at DATETIME, 
	approved_by_user_id INTEGER, 
	approved_at DATETIME, 
	unit_cost_at_close INTEGER, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_stock_take_items_quantities_nonneg CHECK (expected_qty >= 0 AND physical_qty >= 0), 
	CONSTRAINT ck_stock_take_items_cost_nonneg CHECK (unit_cost_at_close >= 0), 
	FOREIGN KEY(stock_take_id) REFERENCES stock_takes (id), 
	FOREIGN KEY(batch_id) REFERENCES medicine_batches (id), 
	FOREIGN KEY(approved_by_user_id) REFERENCES users (id), 
	FOREIGN KEY(counted_by_user_id) REFERENCES users (id), 
	FOREIGN KEY(product_id) REFERENCES products (id)
);
CREATE TABLE "stock_takes" (
	id INTEGER NOT NULL, 
	status VARCHAR(9) DEFAULT 'OPEN' NOT NULL, 
	initiated_by_user_id INTEGER NOT NULL, 
	started_at DATETIME DEFAULT (CURRENT_TIMESTAMP), 
	closed_at DATETIME, 
	notes VARCHAR(255), 
	PRIMARY KEY (id), 
	FOREIGN KEY(initiated_by_user_id) REFERENCES users (id)
);
CREATE TABLE "supplier_transactions" (
	id INTEGER NOT NULL, 
	supplier_id INTEGER NOT NULL, 
	amount INTEGER NOT NULL, 
	reference VARCHAR(100), 
	notes VARCHAR(255), 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP), idempotency_key VARCHAR(64), reverses_transaction_id INTEGER REFERENCES supplier_transactions (id), 
	PRIMARY KEY (id), 
	FOREIGN KEY(supplier_id) REFERENCES suppliers (id)
);
CREATE TABLE suppliers (
	id INTEGER NOT NULL, 
	name VARCHAR(150) NOT NULL, 
	contact_phone VARCHAR(30), 
	contact_email VARCHAR(120), 
	address VARCHAR(255), 
	notes VARCHAR(255), 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP, 
	PRIMARY KEY (id)
);
CREATE TABLE user_sessions (
	id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	refresh_token_jti VARCHAR(64) NOT NULL, 
	device_label VARCHAR(120), 
	ip_address VARCHAR(45), 
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP, 
	revoked_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE TABLE "users" (
	id INTEGER NOT NULL, 
	full_name VARCHAR(120) NOT NULL, 
	username VARCHAR(80) NOT NULL, 
	hashed_password VARCHAR(255) NOT NULL, 
	is_active BOOLEAN DEFAULT 1 NOT NULL, 
	role_id INTEGER NOT NULL, 
	security_question VARCHAR(255), 
	security_answer_hash VARCHAR(255), 
	created_at DATETIME DEFAULT (CURRENT_TIMESTAMP), 
	deleted_at DATETIME, 
	must_change_password BOOLEAN DEFAULT 0 NOT NULL, terms_accepted_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(role_id) REFERENCES roles (id)
);
CREATE INDEX ix_ai_conversation_messages_conversation_id ON ai_conversation_messages (conversation_id);
CREATE INDEX ix_ai_conversation_messages_created_at ON ai_conversation_messages (created_at);
CREATE INDEX ix_ai_conversations_user_id ON ai_conversations (user_id);
CREATE INDEX ix_ai_provider_keys_user_id ON ai_provider_keys (user_id);
CREATE INDEX ix_audit_logs_created_at ON audit_logs (created_at);
CREATE UNIQUE INDEX ix_customers_phone ON customers (phone);
CREATE INDEX ix_medicine_batches_expiry_date ON medicine_batches (expiry_date);
CREATE INDEX ix_medicine_batches_product_id ON medicine_batches (product_id);
CREATE INDEX ix_payments_sale_id ON payments (sale_id);
CREATE UNIQUE INDEX ix_permissions_code ON permissions (code);
CREATE UNIQUE INDEX ix_products_barcode_active_unique ON products (barcode) WHERE deleted_at IS NULL;
CREATE INDEX ix_products_category_id ON products (category_id);
CREATE UNIQUE INDEX ix_products_name_active_unique ON products (name COLLATE NOCASE) WHERE deleted_at IS NULL;
CREATE INDEX ix_purchase_order_items_purchase_order_id ON purchase_order_items (purchase_order_id);
CREATE INDEX ix_purchase_orders_supplier_id ON purchase_orders (supplier_id);
CREATE INDEX ix_refund_items_refund_id ON refund_items (refund_id);
CREATE INDEX ix_refund_items_sale_item_id ON refund_items (sale_item_id);
CREATE INDEX ix_refunds_created_at ON refunds (created_at);
CREATE INDEX ix_refunds_sale_id ON refunds (sale_id);
CREATE INDEX ix_sale_items_product_id ON sale_items (product_id);
CREATE INDEX ix_sale_items_sale_id ON sale_items (sale_id);
CREATE INDEX ix_sales_cashier_user_id ON sales (cashier_user_id);
CREATE INDEX ix_sales_created_at ON sales (created_at);
CREATE INDEX ix_sales_customer_id ON sales (customer_id);
CREATE UNIQUE INDEX ix_sales_idempotency_key ON sales (idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX ix_stock_movements_batch_id ON stock_movements (batch_id);
CREATE INDEX ix_stock_movements_created_at ON stock_movements (created_at);
CREATE INDEX ix_stock_take_items_stock_take_id ON stock_take_items (stock_take_id);
CREATE UNIQUE INDEX ix_supplier_transactions_idempotency_key ON supplier_transactions (idempotency_key);
CREATE UNIQUE INDEX ix_supplier_transactions_reverses_transaction_id ON supplier_transactions (reverses_transaction_id);
CREATE INDEX ix_supplier_transactions_supplier_id ON supplier_transactions (supplier_id);
CREATE INDEX ix_suppliers_name ON suppliers (name);
CREATE UNIQUE INDEX ix_user_sessions_refresh_token_jti ON user_sessions (refresh_token_jti);
CREATE INDEX ix_user_sessions_user_id ON user_sessions (user_id);
CREATE UNIQUE INDEX ix_users_username ON users (username);
