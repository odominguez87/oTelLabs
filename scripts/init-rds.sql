-- Base "reviews" — simula AWS RDS (PostgreSQL). Consumida por data-service.
CREATE TABLE IF NOT EXISTS reviews (
    product_id    VARCHAR(100) PRIMARY KEY,
    avg_rating    NUMERIC(3, 2) NOT NULL,
    review_count  INTEGER NOT NULL DEFAULT 0
);
INSERT INTO reviews (product_id, avg_rating, review_count) VALUES
    ('LAPTOP-X1',   4.60, 1240),
    ('MOUSE-PRO',   4.20,  860),
    ('KEYBOARD-MK', 4.40,  410),
    ('MONITOR-4K',  4.70,  305),
    ('HEADSET-Z',   3.90,  198)
ON CONFLICT (product_id) DO NOTHING;
