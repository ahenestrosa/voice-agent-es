-- A second, disposable database for pytest, so test runs never touch the demo data in `bank`.
-- The schema itself lives in Alembic migrations (migrations/), not here.
CREATE DATABASE IF NOT EXISTS bank_test CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
GRANT ALL PRIVILEGES ON bank_test.* TO 'bank'@'%';
