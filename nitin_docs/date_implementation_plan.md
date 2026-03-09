# DATE/DATETIME Support Evaluation for sql-redis

## Executive Summary
Evaluation of DATE/DATETIME support options for sql-redis based on Redis 8.x RediSearch capabilities.

## Redis 8.x DATE/DATETIME Capabilities

### Key Finding
**Redis RediSearch does NOT have a native DATE or DATETIME field type.**

Dates must be stored and queried using one of these approaches:
1. **NUMERIC fields** with Unix timestamps (recommended)
2. **TAG fields** for exact date matching
3. **TEXT fields** for date string searching (not recommended)

## Current Status

### What Already Works
Since dates are stored as NUMERIC fields with Unix timestamps, the existing sql-redis implementation already supports date queries:

```sql
-- Date range query (Unix timestamps)
SELECT * FROM events WHERE created_at > 1704067200 AND created_at < 1706745600

-- Exact timestamp match
SELECT * FROM events WHERE event_date = 1704067200

-- BETWEEN for date ranges
SELECT * FROM events WHERE created_at BETWEEN 1704067200 AND 1706745600
```

### What's Missing
1. **Date literal parsing**: Converting `'2024-01-01'` to Unix timestamp `1704067200`
2. **Date functions**: `DATE_ADD()`, `DATE_SUB()`, `YEAR()`, `MONTH()`, etc.
3. **Date formatting**: Converting timestamps back to readable dates in results

## Recommendation

### Decision: Defer to Future Release

**Rationale:**
1. **Core functionality works** - NUMERIC range queries already handle date comparisons
2. **Complexity vs. value** - Date literal parsing adds significant complexity for marginal benefit
3. **User workaround exists** - Users can convert dates to timestamps in application code
4. **No Redis support** - Redis doesn't provide date-specific features to leverage

### Workaround for Users

```python
from datetime import datetime

# Convert date to Unix timestamp
date_str = "2024-01-01"
timestamp = int(datetime.strptime(date_str, "%Y-%m-%d").timestamp())

# Use in SQL query
sql = f"SELECT * FROM events WHERE created_at > {timestamp}"
```

## Future Implementation (If Needed)

### Phase 1: Date Literal Parsing
- Parse ISO 8601 date strings: `'2024-01-01'`, `'2024-01-01T12:00:00'`
- Convert to Unix timestamps during SQL parsing
- Requires: Parser enhancement to detect date literals

### Phase 2: Date Functions
- `DATE_ADD(field, INTERVAL n DAY/MONTH/YEAR)`
- `DATE_SUB(field, INTERVAL n DAY/MONTH/YEAR)`
- `YEAR(field)`, `MONTH(field)`, `DAY(field)`
- Requires: Computed field support with timestamp arithmetic

### Phase 3: Date Formatting
- `DATE_FORMAT(field, '%Y-%m-%d')` in SELECT
- Requires: Post-processing of results

## Files to Modify (Future)

| File | Changes |
|------|---------|
| `sql_redis/parser.py` | Date literal detection and conversion |
| `sql_redis/translator.py` | Date function handling |
| `tests/test_date_fields.py` | New test suite |

## Acceptance Criteria (Future)

- [ ] Date literals converted to timestamps
- [ ] Date range queries work with literals
- [ ] Date functions supported in WHERE clause
- [ ] All existing tests pass

## Documentation Update

Add to README.md:

```markdown
### DATE/DATETIME Handling

Redis does not have a native DATE field type. Dates should be stored as:
- **NUMERIC fields** with Unix timestamps (recommended for range queries)
- **TAG fields** for exact date matching

Example: `WHERE created_at > 1704067200` (Unix timestamp for 2024-01-01)
```

