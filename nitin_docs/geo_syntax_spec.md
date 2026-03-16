# GEO Syntax Specification for sql-redis

## Overview

This document specifies the GEO query syntax for the `sql-redis` library, including coordinate order, units, and operator support.

## 1. Coordinate Order: `POINT(lon, lat)`

### Decision
Use **longitude-first** order: `POINT(longitude, latitude)` to match Redis's native format.

### Rationale

| System | Coordinate Order | Notes |
|--------|------------------|-------|
| Redis GEOFILTER | `lon, lat` | Internal Redis format |
| GeoJSON standard | `[lon, lat]` | W3C/IETF standard |
| redisvl GeoRadius | `lon, lat` | Matches Redis |
| sql-redis POINT() | `lon, lat` | Matches Redis |

**Consistency with Redis wins.** Using the same coordinate order as Redis:
- No confusion when debugging Redis commands
- Matches redisvl's native `GeoRadius(lon, lat)` API
- Follows GeoJSON standard

### Implementation
The parser accepts `POINT(lon, lat)` and passes directly to Redis (no swap needed):

```sql
-- User writes (lon, lat)
SELECT * FROM stores WHERE geo_distance(location, POINT(-122.4194, 37.7749)) < 5000

-- Directly translated to Redis (lon, lat)
FT.SEARCH stores "*" GEOFILTER location -122.4194 37.7749 5000 m
```

## 2. Units

### Supported Units

| Unit | Code | Description | Conversion |
|------|------|-------------|------------|
| Meters | `m` | SI standard (DEFAULT) | 1 m |
| Kilometers | `km` | Metric | 1000 m |
| Miles | `mi` | Imperial | ~1609.34 m |
| Feet | `ft` | Imperial | ~0.3048 m |

### Default Unit: Meters (`m`)

**Rationale:**
- SQL/MM spatial standard uses meters
- PostGIS `ST_Distance` returns meters
- MySQL `ST_Distance_Sphere` returns meters
- BigQuery `ST_DISTANCE` returns meters
- Consistent with scientific/engineering standards

### Syntax

```sql
-- Default: meters
geo_distance(location, POINT(-122.4194, 37.7749)) < 5000

-- Explicit unit
geo_distance(location, POINT(-122.4194, 37.7749), 'm') < 5000
geo_distance(location, POINT(-122.4194, 37.7749), 'km') < 5
geo_distance(location, POINT(-122.4194, 37.7749), 'mi') < 3
geo_distance(location, POINT(-122.4194, 37.7749), 'ft') < 16400
```

## 3. Operator Support

### Supported Operators

| Operator | Example | Redis Implementation |
|----------|---------|---------------------|
| `<` | `geo_distance(...) < 5000` | `FT.SEARCH` with `GEOFILTER` |
| `<=` | `geo_distance(...) <= 5000` | `FT.SEARCH` with `GEOFILTER` |
| `>` | `geo_distance(...) > 5000` | `FT.AGGREGATE` with `FILTER` |
| `>=` | `geo_distance(...) >= 5000` | `FT.AGGREGATE` with `FILTER` |
| `BETWEEN` | `geo_distance(...) BETWEEN 1000 AND 5000` | `FT.AGGREGATE` with `FILTER` |

### Implementation Strategy

**Optimized path (`<`, `<=`):**
Uses Redis `GEOFILTER` which leverages the spatial index for fast radius queries.

```sql
SELECT name FROM stores WHERE geo_distance(location, POINT(-122.4194, 37.7749)) < 5000
```
```
FT.SEARCH stores "*" GEOFILTER location -122.4194 37.7749 5000 m RETURN 1 name
```

**Aggregate path (`>`, `>=`, `BETWEEN`):**
Uses `FT.AGGREGATE` with `APPLY geodistance()` and `FILTER` for distance comparisons.

```sql
SELECT name FROM stores WHERE geo_distance(location, POINT(-122.4194, 37.7749)) > 5000
```
```
FT.AGGREGATE stores "*" LOAD 1 location APPLY "geodistance(@location, -122.4194, 37.7749)" AS __geo_dist FILTER "@__geo_dist > 5000"
```

**BETWEEN example:**
```sql
SELECT name FROM stores WHERE geo_distance(location, POINT(-122.4194, 37.7749)) BETWEEN 1000 AND 5000
```
```
FT.AGGREGATE stores "*" LOAD 1 location APPLY "geodistance(@location, -122.4194, 37.7749)" AS __geo_dist FILTER "@__geo_dist >= 1000 && @__geo_dist <= 5000"
```

## 4. Complete Syntax Reference

### WHERE Clause (Filtering)

```sql
-- Basic radius query (meters, default)
WHERE geo_distance(field, POINT(lon, lat)) < radius

-- With explicit unit
WHERE geo_distance(field, POINT(lon, lat), 'unit') < radius

-- All operators
WHERE geo_distance(field, POINT(lon, lat), 'km') < 50
WHERE geo_distance(field, POINT(lon, lat), 'km') <= 50
WHERE geo_distance(field, POINT(lon, lat), 'km') > 50
WHERE geo_distance(field, POINT(lon, lat), 'km') >= 50
WHERE geo_distance(field, POINT(lon, lat), 'km') BETWEEN 10 AND 50
```

### SELECT Clause (Distance Calculation)

```sql
-- Calculate distance (returns meters by default)
SELECT name, geo_distance(location, POINT(-122.4194, 37.7749)) AS distance FROM stores

-- With unit (converts result to specified unit)
SELECT name, geo_distance(location, POINT(-122.4194, 37.7749), 'km') AS distance_km FROM stores
```

### Combined Queries

```sql
-- GEO + TAG filter
SELECT name FROM stores
WHERE category = 'retail' AND geo_distance(location, POINT(-122.4194, 37.7749)) < 5000

-- GEO + NUMERIC filter
SELECT name FROM stores
WHERE rating >= 4.0 AND geo_distance(location, POINT(-122.4194, 37.7749), 'mi') < 10

-- Distance in SELECT with filter
SELECT name, geo_distance(location, POINT(-122.4194, 37.7749)) AS dist
FROM stores
WHERE geo_distance(location, POINT(-122.4194, 37.7749)) < 50000
```

## 5. Notes

This is a new feature - GEO support does not exist on the main branch.

### Key Design Decisions

| Aspect | Decision | Rationale |
|--------|----------|-----------|
| Coordinate order | `POINT(lon, lat)` | Matches Redis native format |
| Default unit | `m` (meters) | SQL standard |
| Operators | `<`, `<=`, `>`, `>=`, `BETWEEN` | Full comparison support |

