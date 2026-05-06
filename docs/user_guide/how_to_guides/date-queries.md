# Date and datetime queries

You want to filter on dates, even though Redis has no native `DATE` type.

## How dates are stored

Dates are stored as `NUMERIC` fields containing Unix timestamps. sql-redis converts ISO 8601 string literals to timestamps automatically.

```sql
-- '2024-01-01' becomes 1704067200
SELECT * FROM events WHERE created_at > '2024-01-01'

-- Datetime with time
SELECT * FROM events WHERE created_at > '2024-01-01T12:00:00'

-- Range
SELECT * FROM events WHERE created_at BETWEEN '2024-01-01' AND '2024-01-31'
```

## Supported literal formats

- Date: `'2024-01-01'` (interpreted as midnight UTC)
- Datetime: `'2024-01-01T12:00:00'` or `'2024-01-01 12:00:00'`
- Datetime with timezone: `'2024-01-01T12:00:00Z'`, `'2024-01-01T12:00:00+00:00'`
- Raw timestamp: `1704067200`

Timezone-naive literals are interpreted as UTC.

## Date functions

Extract date parts using SQL functions that map to Redis `APPLY`:

| SQL function | Redis function | Description |
|---|---|---|
| `YEAR(field)` | `year(@field)` | Extract year |
| `MONTH(field)` | `monthofyear(@field)` | Month, **0-11** |
| `DAY(field)` | `dayofmonth(@field)` | Day of month, 1-31 |
| `HOUR(field)` | `hour(@field)` | Round to hour |
| `MINUTE(field)` | `minute(@field)` | Round to minute |
| `DAYOFWEEK(field)` | `dayofweek(@field)` | **0 = Sunday** |
| `DAYOFYEAR(field)` | `dayofyear(@field)` | 0-365 |
| `DATE_FORMAT(field, fmt)` | `timefmt(@field, fmt)` | Format timestamp |

## Examples

```sql
-- Extract parts
SELECT name, YEAR(created_at) AS y, MONTH(created_at) AS m FROM events

-- Filter by year
SELECT name FROM events WHERE YEAR(created_at) = 2024

-- Group by year
SELECT YEAR(created_at) AS year, COUNT(*) FROM events GROUP BY year

-- Format
SELECT name, DATE_FORMAT(created_at, '%Y-%m-%d') AS date FROM events
```

## Limitations

- `NOT YEAR(field) = 2024` raises `ValueError`.
- `DATE_FORMAT()` is `SELECT`-only. It is not supported in `WHERE`.
- Date functions combined with `OR` raise `ValueError`.
