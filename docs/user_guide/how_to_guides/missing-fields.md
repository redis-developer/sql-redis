# Check for missing fields

You want to find documents where a field is absent or present.

## `IS NULL` and `IS NOT NULL`

```sql
-- Find users without an email
SELECT * FROM users WHERE email IS NULL

-- Find users with an email
SELECT * FROM users WHERE email IS NOT NULL

-- Combine with other filters
SELECT * FROM users WHERE category = 'eng' AND email IS NULL
```

`IS NULL` translates to `ismissing(@email)` and the negation to `-ismissing(@email)`.

**Requires Redis 7.4+** (RediSearch 2.10+) and the field must be declared with `INDEXMISSING` in the index schema. A `UserWarning` is emitted at translation time as a reminder.

## `exists()` for `SELECT` and `HAVING`

`exists()` is a different mechanism. It runs through `FT.AGGREGATE` and `APPLY`, returning `1` or `0` per row rather than filtering them out.

```sql
-- Add a 0/1 column
SELECT name, exists(email) AS has_email FROM users

-- Filter via HAVING
SELECT name FROM users HAVING exists(email) = 1

-- Multiple checks
SELECT name, exists(email) AS has_email, exists(phone) AS has_phone FROM users
```

`exists()` does not require `INDEXMISSING` on the field, but it cannot be used in `WHERE`. Use `IS NULL` / `IS NOT NULL` to filter rows by presence.
