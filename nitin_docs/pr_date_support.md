# PR: DATE/DATETIME Support

## Summary

Adds comprehensive DATE/DATETIME support to sql-redis, enabling date filtering with ISO 8601 literals and date part extraction using SQL functions.

**This is a new feature** - DATE/DATETIME support does not exist on the main branch.

## New Features

### 1. Date Literal Parsing (Phase 1)

Use ISO 8601 date strings directly in WHERE clauses:

```sql
-- Date literal automatically converted to Unix timestamp
SELECT * FROM events WHERE created_at > '2024-01-01'

-- With time component
SELECT * FROM events WHERE created_at > '2024-01-01T12:00:00'

-- Date ranges
SELECT * FROM events WHERE created_at BETWEEN '2024-01-01' AND '2024-03-31'
```

### 2. Date Extraction Functions (Phase 2)

Extract date parts using SQL functions that map to Redis APPLY expressions:

| SQL Function | Redis Function | Returns |
|--------------|----------------|---------|
| `YEAR(field)` | `year(@field)` | Year (e.g., 2024) |
| `MONTH(field)` | `monthofyear(@field)` | Month (0-11) |
| `DAY(field)` | `dayofmonth(@field)` | Day of month (1-31) |
| `HOUR(field)` | `hour(@field)` | Hour (rounds timestamp) |
| `MINUTE(field)` | `minute(@field)` | Minute (rounds timestamp) |
| `DAYOFWEEK(field)` | `dayofweek(@field)` | Day of week (0=Sunday) |
| `DAYOFYEAR(field)` | `dayofyear(@field)` | Day of year (0-365) |

```sql
-- Extract year and month
SELECT name, YEAR(created_at) AS year, MONTH(created_at) AS month FROM events

-- Filter by date parts
SELECT * FROM events WHERE YEAR(created_at) = 2024 AND MONTH(created_at) >= 6
```

### 3. Date Formatting (Phase 3)

Format timestamps as human-readable strings:

```sql
SELECT name, DATE_FORMAT(created_at, '%Y-%m-%d') AS date FROM events
```

Maps to Redis's `timefmt(@field, format)` function.

### 4. GROUP BY Date Parts

Aggregate data by date components:

```sql
-- Count events per year
SELECT YEAR(created_at) AS year, COUNT(*) FROM events GROUP BY year

-- Events per month per category
SELECT category, MONTH(created_at) AS month, COUNT(*) 
FROM events GROUP BY category, month
```

## Files Changed

| File | Changes |
|------|---------|
| `sql_redis/parser.py` | DateFunctionSpec dataclass, date literal detection, date function parsing |
| `sql_redis/translator.py` | APPLY generation for date functions, FILTER for date conditions |
| `sql_redis/analyzer.py` | Date function field resolution, alias handling for GROUP BY |
| `tests/test_date_fields.py` | 11 tests for date literal parsing |
| `tests/test_date_functions.py` | 14 tests for date functions |
| `README.md` | Comprehensive date documentation |

## What's NOT Supported (and Why)

### DATE_ADD / DATE_SUB

```sql
-- NOT SUPPORTED
SELECT * FROM events WHERE created_at > DATE_SUB(NOW(), INTERVAL 7 DAY)
```

**Why:** Redis RediSearch has no native date arithmetic functions. This would require:
1. Computing `NOW()` at query time
2. Translating `INTERVAL` syntax to seconds
3. Generating arithmetic like `@field + 604800`

**Workaround:** Compute the timestamp in application code:
```python
from datetime import datetime, timedelta
cutoff = int((datetime.now() - timedelta(days=7)).timestamp())
sql = f"SELECT * FROM events WHERE created_at > {cutoff}"
```

### SECOND()

```sql
-- NOT SUPPORTED
SELECT SECOND(created_at) FROM events
```

**Why:** Redis RediSearch doesn't have a `second()` function. The available time functions are:
- `hour()` - rounds to hour
- `minute()` - rounds to minute

No sub-minute extraction is available.

### NOW() / CURRENT_TIMESTAMP

```sql
-- NOT SUPPORTED
SELECT * FROM events WHERE created_at > NOW()
```

**Why:** These are dynamic values that must be evaluated at query time. Redis queries are static - there's no concept of "current time" in the query language.

**Workaround:** Pass the current timestamp from application code:
```python
import time
now = int(time.time())
sql = f"SELECT * FROM events WHERE created_at > {now}"
```

## Redis Implementation Details

### Storage Format

Dates are stored as **Unix timestamps** in **NUMERIC fields**:

```python
# Store
redis.hset("event:1", mapping={
    "name": "Meeting",
    "created_at": 1704067200  # 2024-01-01 00:00:00 UTC
})
```

### Query Routing

| Feature | Redis Command |
|---------|---------------|
| Date literals in WHERE | `FT.SEARCH` with numeric range |
| Date functions in SELECT | `FT.AGGREGATE` with APPLY |
| Date functions in WHERE | `FT.AGGREGATE` with APPLY + FILTER |
| GROUP BY date parts | `FT.AGGREGATE` with GROUPBY |

### MONTH Returns 0-11

**Important:** Redis's `monthofyear()` returns 0-11, not 1-12:

| Month | Redis Value |
|-------|-------------|
| January | 0 |
| February | 1 |
| ... | ... |
| December | 11 |

```sql
-- Find January events (month = 0, not 1)
SELECT * FROM events WHERE MONTH(created_at) = 0
```

## No Breaking Changes

This PR adds new functionality only. No existing APIs are modified.

## Test Results

```
✅ 273 tests passed
✅ 14 new date function tests
✅ 11 date literal tests
✅ No regressions
```

## Usage Examples

### Date Literals
```sql
-- After a date
SELECT * FROM events WHERE created_at > '2024-01-01'

-- Before a date
SELECT * FROM events WHERE created_at < '2024-12-31'

-- Date range
SELECT * FROM events WHERE created_at BETWEEN '2024-01-01' AND '2024-06-30'

-- With timestamp
SELECT * FROM events WHERE created_at > '2024-01-01T09:00:00'
```

### Date Functions in SELECT
```sql
-- Single function
SELECT name, YEAR(created_at) AS year FROM events

-- Multiple functions
SELECT name, YEAR(created_at) AS y, MONTH(created_at) AS m, DAY(created_at) AS d
FROM events

-- Formatted output
SELECT name, DATE_FORMAT(created_at, '%Y-%m-%d %H:%M') AS datetime FROM events
```

### Date Functions in WHERE
```sql
-- Filter by year
SELECT * FROM events WHERE YEAR(created_at) = 2024

-- Filter by month range
SELECT * FROM events WHERE MONTH(created_at) >= 6

-- Combined conditions
SELECT * FROM events WHERE YEAR(created_at) = 2024 AND MONTH(created_at) = 0
```

### Aggregations
```sql
-- Count per year
SELECT YEAR(created_at) AS year, COUNT(*) AS total
FROM events GROUP BY year

-- Count per category per year
SELECT category, YEAR(created_at) AS year, COUNT(*) AS cnt
FROM events GROUP BY category, year
```

### Combined with Other Filters
```sql
-- Date + category filter
SELECT name FROM events
WHERE category = 'meeting' AND created_at > '2024-01-01'

-- Date function + category
SELECT name FROM events
WHERE category = 'release' AND YEAR(created_at) = 2024
```

## Quality Checks

```
✅ All tests pass (273/273)
✅ README.md updated
✅ Demonstration notebook created
```

---

**Ready to merge** ✅
