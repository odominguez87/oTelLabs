-- Base "catalog" — simula GCP Cloud SQL (PostgreSQL). Consumida por data-service.
CREATE TABLE IF NOT EXISTS pricing (
    product_id  VARCHAR(100) PRIMARY KEY,
    price       NUMERIC(10, 2) NOT NULL,
    currency    CHAR(3) NOT NULL DEFAULT 'USD',
    updated_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
INSERT INTO pricing (product_id, price, currency) VALUES
    ('LAPTOP-X1',   1299.00, 'USD'),
    ('MOUSE-PRO',     49.90, 'USD'),
    ('KEYBOARD-MK',   89.00, 'USD'),
    ('MONITOR-4K',   459.00, 'USD'),
    ('HEADSET-Z',    129.00, 'USD')
ON CONFLICT (product_id) DO NOTHING;
