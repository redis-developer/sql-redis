# PR: GEO Field Support

## Summary

Adds full GEO field support to sql-redis, enabling location-based queries using familiar SQL syntax.

**This is a new feature** - GEO support does not exist on the main branch.

## New Features

### 1. `geo_distance()` Function

Query by geographic distance using `POINT(lon, lat)` syntax:

```sql
-- Find stores within 5km of San Francisco
SELECT name FROM stores
WHERE geo_distance(location, POINT(-122.4194, 37.7749), 'km') < 5
```

### 2. Coordinate Order: `POINT(lon, lat)`

Uses **longitude-first** order, matching Redis's native format:

```sql
-- San Francisco: lon=-122.4194, lat=37.7749
SELECT * FROM stores WHERE geo_distance(location, POINT(-122.4194, 37.7749)) < 5000
```

### 3. Default Unit: Meters

Aligns with SQL spatial standards (PostGIS, MySQL, BigQuery all use meters):

```sql
-- Default: meters
WHERE geo_distance(location, POINT(-122.4194, 37.7749)) < 5000

-- Explicit units: m, km, mi, ft
WHERE geo_distance(location, POINT(-122.4194, 37.7749), 'km') < 5
WHERE geo_distance(location, POINT(-122.4194, 37.7749), 'mi') < 3
```

### 4. Full Operator Support

| Operator | Redis Implementation |
|----------|---------------------|
| `<`, `<=` | `FT.SEARCH` with `GEOFILTER` (optimized) |
| `>`, `>=`, `BETWEEN` | `FT.AGGREGATE` with `FILTER` |

```sql
-- Find stores MORE than 100km away
SELECT name FROM stores WHERE geo_distance(location, POINT(-122.4194, 37.7749)) > 100000

-- Find stores between 10-50km
SELECT name FROM stores WHERE geo_distance(location, POINT(-122.4194, 37.7749), 'km') BETWEEN 10 AND 50
```

### 5. Distance Calculation in SELECT

```sql
SELECT name, geo_distance(location, POINT(-122.4194, 37.7749)) AS distance
FROM stores
```

## Files Changed

| File | Changes |
|------|---------|
| `sql_redis/parser.py` | Add GeoDistanceCondition, GeoDistanceSelect, parsing logic |
| `sql_redis/translator.py` | Add GEOFILTER generation, FT.AGGREGATE with FILTER |
| `sql_redis/analyzer.py` | Minor updates for geo field handling |
| `tests/test_geo_fields.py` | New test file with 12 GEO tests |
| `tests/test_sql_parser.py` | Add geo_distance parsing test |
| `README.md` | Comprehensive GEO documentation |

## No Breaking Changes

This PR adds new functionality only. No existing APIs are modified.

## Test Results

```
✅ 258 tests passed
✅ 4 new GEO operator tests added
✅ No regressions
```

## Usage Examples

### Basic Radius Query (meters)
```sql
SELECT name FROM stores
WHERE geo_distance(location, POINT(-122.4194, 37.7749)) < 5000
```

### With Explicit Unit
```sql
SELECT name FROM stores
WHERE geo_distance(location, POINT(-122.4194, 37.7749), 'km') < 5
```

### All Supported Units
```sql
-- Meters (default)
WHERE geo_distance(location, POINT(-122.4194, 37.7749)) < 5000

-- Kilometers
WHERE geo_distance(location, POINT(-122.4194, 37.7749), 'km') < 5

-- Miles
WHERE geo_distance(location, POINT(-122.4194, 37.7749), 'mi') < 3

-- Feet
WHERE geo_distance(location, POINT(-122.4194, 37.7749), 'ft') < 16400
```

### All Operators
```sql
-- Less than (optimized GEOFILTER)
WHERE geo_distance(location, POINT(-122.4194, 37.7749)) < 5000

-- Less than or equal (optimized GEOFILTER)
WHERE geo_distance(location, POINT(-122.4194, 37.7749)) <= 5000

-- Greater than (FT.AGGREGATE)
WHERE geo_distance(location, POINT(-122.4194, 37.7749)) > 100000

-- Greater than or equal (FT.AGGREGATE)
WHERE geo_distance(location, POINT(-122.4194, 37.7749)) >= 100000

-- Between (FT.AGGREGATE)
WHERE geo_distance(location, POINT(-122.4194, 37.7749), 'km') BETWEEN 10 AND 100
```

### Distance Calculation
```sql
SELECT name, geo_distance(location, POINT(-122.4194, 37.7749)) AS distance
FROM stores
```

### Combined Filters
```sql
SELECT name FROM stores
WHERE category = 'retail'
  AND rating >= 4.0
  AND geo_distance(location, POINT(-122.4194, 37.7749)) < 5000
```

## Quality Checks

```
✅ All tests pass (258/258)
✅ README.md updated
✅ Specification documented
```

---

**Ready to merge** ✅

