# SQL Notes — Beginner Edition (Sections 1–8) 📚

> A compact SQL starter sheet with clear examples and best practices.
> Focus: ANSI SQL basics (with notes for PostgreSQL / MySQL / SQL Server when useful).

---

## 📑 Table of Contents (Beginner)
- [Section 1 — 🧭 SQL Overview & Syntax](#sec1)
- [Section 2 — 🗄️ DDL: Create / Alter / Drop](#sec2)
- [Section 3 — ✍️ DML: Insert / Update / Delete](#sec3)
- [Section 4 — 🔎 Querying: Select, Where, Order, Limit](#sec4)
- [Section 5 — 🧮 Aggregation: Group By, Having](#sec5)
- [Section 6 — 🤝 Joins (INNER/LEFT/RIGHT/FULL)](#sec6)
- [Section 7 — 🧩 Subqueries & CTEs (WITH)](#sec7)
- [Section 8 — 📦 Set Operations (UNION/UNION ALL/EXCEPT/INTERSECT)](#sec8)

---

<a id="sec1"></a>
## ################### Section 1 — 🧭 SQL Overview & Syntax

<details><summary><strong>Overview</strong></summary>
SQL = Structured Query Language. You’ll mainly write SELECT queries to read data, and some DDL/DML to define and modify tables.
</details>

```sql
-- A statement usually ends with a semicolon ;
SELECT 1;
```

> 💡 **Tip** — Use readable formatting: UPPERCASE keywords, indent joins/conditions, one clause per line.

---

<a id="sec2"></a>
## ################### Section 2 — 🗄️ DDL: Create / Alter / Drop

```sql
CREATE TABLE customers (
  customer_id   BIGINT PRIMARY KEY,
  name          VARCHAR(200) NOT NULL,
  email         VARCHAR(255) UNIQUE,
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE customers ADD COLUMN phone VARCHAR(32);
-- PostgreSQL:
ALTER TABLE customers ALTER COLUMN name SET NOT NULL;
-- MySQL:
-- ALTER TABLE customers MODIFY name VARCHAR(200) NOT NULL;

DROP TABLE IF EXISTS customers;
```

> ⚠️ **Warning** — DDL can lock tables and is often irreversible in production.
> 💡 **Tip** — Use `IF EXISTS` / `IF NOT EXISTS` where supported to avoid errors.

---

<a id="sec3"></a>
## ################### Section 3 — ✍️ DML: Insert / Update / Delete

```sql
INSERT INTO customers (customer_id, name, email)
VALUES (1, 'Alice', 'alice@x.io'), (2, 'Bob', 'bob@x.io');

UPDATE customers
SET email = 'alice@new.io'
WHERE customer_id = 1;

DELETE FROM customers
WHERE customer_id = 2;
```

> 💡 **Tip** — Always include a `WHERE` in `UPDATE`/`DELETE` unless you truly want to affect all rows.

---

<a id="sec4"></a>
## ################### Section 4 — 🔎 Querying: Select, Where, Order, Limit

```sql
SELECT c.customer_id, c.name, c.email
FROM customers AS c
WHERE c.email LIKE '%@x.io'
  AND c.created_at >= DATE '2024-01-01'
ORDER BY c.created_at DESC
FETCH FIRST 10 ROWS ONLY;       -- ANSI; PostgreSQL uses LIMIT 10; MySQL also LIMIT 10; SQL Server TOP 10
```

> 💡 **Tip** — Select only needed columns; filter early to reduce scanned data.
> ⚠️ **Warning** — Leading `%` in `LIKE '%text'` often disables index usage.

---

<a id="sec5"></a>
## ################### Section 5 — 🧮 Aggregation: Group By, Having

```sql
SELECT DATE(created_at) AS day, COUNT(*) AS new_customers
FROM customers
GROUP BY DATE(created_at)
HAVING COUNT(*) > 10
ORDER BY day;
```

> 💡 **Tip** — `WHERE` filters rows **before** grouping; `HAVING` filters **after** aggregation.
> ⚠️ **Rule** — In ANSI SQL, every non-aggregated selected column must be in `GROUP BY`.

---

<a id="sec6"></a>
## ################### Section 6 — 🤝 Joins (INNER/LEFT/RIGHT/FULL)

```sql
SELECT o.order_id, c.name, o.total_amount
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id         -- INNER (match required)
LEFT JOIN payments p ON p.order_id = o.order_id;           -- optional related rows
-- RIGHT JOIN and FULL OUTER JOIN exist (note: MySQL has no FULL OUTER JOIN)
```

> 💡 **Tip** — Use `INNER JOIN` when relationship is mandatory, `LEFT JOIN` when optional.
> ⚠️ **Warning** — Always join on a proper key; accidental cross joins explode row counts.

---

<a id="sec7"></a>
## ################### Section 7 — 🧩 Subqueries & CTEs (WITH)

```sql
-- CTE to get the latest order per customer
WITH latest_orders AS (
  SELECT o.*,
         ROW_NUMBER() OVER (PARTITION BY o.customer_id ORDER BY o.created_at DESC) AS rn
  FROM orders o
)
SELECT *
FROM latest_orders
WHERE rn = 1;
```

> 💡 **Tip** — CTEs make complex queries easier to read. Learn them early; they’re very useful.

---

<a id="sec8"></a>
## ################### Section 8 — 📦 Set Operations (UNION/UNION ALL/EXCEPT/INTERSECT)

```sql
SELECT email FROM newsletter_subs
UNION                   -- removes duplicates
SELECT email FROM users;

SELECT email FROM a
UNION ALL               -- keeps duplicates (faster)
SELECT email FROM b;

SELECT id FROM a
EXCEPT
SELECT id FROM b;

SELECT id FROM a
INTERSECT
SELECT id FROM b;
-- MySQL lacks INTERSECT/EXCEPT; emulate with JOINs or DISTINCT + IN
```

> 💡 **Tip** — Prefer `UNION ALL` when you don’t need deduplication.

---

## ✅ Quick Starter Reminders
- Avoid `SELECT *` — choose columns explicitly.
- Always have a primary key on OLTP tables.
- Use `LIMIT`/`TOP`/`FETCH FIRST` when exploring to avoid huge results.
- Practice JOINs and GROUP BY — they’re the backbone of analytics queries.

---

_© Your SQL beginner sheet — focused, readable, and practical._