# GEO queries

You want to filter or sort by geographic distance.

## Coordinate order

Use **longitude first**, matching Redis's native GEO format:

```sql
-- San Francisco: lon=-122.4194, lat=37.7749
SELECT name FROM stores
WHERE geo_distance(location, POINT(-122.4194, 37.7749)) < 5000
```

The default unit is meters.

## Units

| Unit | Code | Example |
|---|---|---|
| Meters | `m` | `geo_distance(location, POINT(-122.4194, 37.7749)) < 5000` |
| Kilometers | `km` | `geo_distance(location, POINT(-122.4194, 37.7749), 'km') < 5` |
| Miles | `mi` | `geo_distance(location, POINT(-122.4194, 37.7749), 'mi') < 3` |
| Feet | `ft` | `geo_distance(location, POINT(-122.4194, 37.7749), 'ft') < 16400` |

## Operators

All comparison operators are supported:

```sql
-- Less than (uses optimized GEOFILTER)
WHERE geo_distance(location, POINT(-122.4194, 37.7749)) < 5000

-- Less than or equal (uses optimized GEOFILTER)
WHERE geo_distance(location, POINT(-122.4194, 37.7749)) <= 5000

-- Greater than (uses FT.AGGREGATE with FILTER)
WHERE geo_distance(location, POINT(-122.4194, 37.7749)) > 100000

-- Between (uses FT.AGGREGATE with FILTER)
WHERE geo_distance(location, POINT(-122.4194, 37.7749), 'km') BETWEEN 10 AND 100
```

## Distance in SELECT

Compute the distance for every result:

```sql
SELECT name, geo_distance(location, POINT(-122.4194, 37.7749)) AS distance
FROM stores
```

## Combine with other filters

```sql
SELECT name FROM stores
WHERE category = 'retail'
  AND geo_distance(location, POINT(-122.4194, 37.7749)) < 5000
```
