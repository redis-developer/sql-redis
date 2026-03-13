"""Tests for GEO field support in sql-redis (TDD - failing tests first)."""

import pytest
import redis

from sql_redis.schema import SchemaRegistry
from sql_redis.translator import Translator


@pytest.fixture
def geo_index(redis_client: redis.Redis) -> str:
    """Create an index with GEO field for testing."""
    index_name = "test_geo_stores"
    try:
        redis_client.execute_command("FT.DROPINDEX", index_name, "DD")
    except redis.ResponseError:
        pass
    redis_client.execute_command(
        "FT.CREATE",
        index_name,
        "ON",
        "HASH",
        "PREFIX",
        "1",
        "store:",
        "SCHEMA",
        "name",
        "TEXT",
        "SORTABLE",
        "category",
        "TAG",
        "location",
        "GEO",
    )
    return index_name


@pytest.fixture
def geo_translator(redis_client: redis.Redis, geo_index: str) -> Translator:
    """Create translator with geo index schema."""
    registry = SchemaRegistry(redis_client)
    registry.refresh(geo_index)
    return Translator(registry)


class TestGeoDistanceLessThan:
    """Tests for geo_distance() < radius queries.

    Note: POINT(lon, lat) matches Redis's native format.
    """

    def test_geo_distance_generates_geofilter(self, geo_translator, geo_index):
        """geo_distance < radius should generate GEOFILTER."""
        # POINT(lon, lat) - matches Redis native format
        sql = f"SELECT name FROM {geo_index} WHERE geo_distance(location, POINT(-122.4, 37.8)) < 10"
        result = geo_translator.translate(sql)
        assert result.command == "FT.SEARCH"
        assert "GEOFILTER" in result.args

    def test_geo_distance_with_km_unit(self, geo_translator, geo_index):
        """geo_distance with km unit."""
        # POINT(lon, lat) - matches Redis native format
        sql = f"SELECT name FROM {geo_index} WHERE geo_distance(location, POINT(-122.4, 37.8), 'km') < 50"
        result = geo_translator.translate(sql)
        assert "km" in " ".join(str(a) for a in result.args).lower()


class TestGeoWithOtherConditions:
    """Tests for combining GEO filters with other field types."""

    def test_geo_with_text_filter(self, geo_translator, geo_index):
        """GEO filter combined with TEXT search."""
        # POINT(lon, lat) - matches Redis native format
        sql = f"SELECT name FROM {geo_index} WHERE name = 'Downtown' AND geo_distance(location, POINT(-122.4, 37.8)) < 10"
        result = geo_translator.translate(sql)
        assert "@name" in result.query_string
        assert "GEOFILTER" in result.args

    def test_geo_with_tag_filter(self, geo_translator, geo_index):
        """GEO filter combined with TAG filter."""
        # POINT(lon, lat) - matches Redis native format
        sql = f"SELECT name FROM {geo_index} WHERE category = 'retail' AND geo_distance(location, POINT(-122.4, 37.8)) < 50"
        result = geo_translator.translate(sql)
        assert "@category:{retail}" in result.query_string
        assert "GEOFILTER" in result.args


class TestGeoDistanceInSelect:
    """Tests for geo_distance() in SELECT clause (FT.AGGREGATE)."""

    def test_geo_distance_in_select_generates_apply(self, geo_translator, geo_index):
        """SELECT geo_distance() AS dist should generate APPLY geodistance()."""
        # POINT(lon, lat) - matches Redis native format
        sql = f"SELECT name, geo_distance(location, POINT(-122.4, 37.8)) AS dist FROM {geo_index}"
        result = geo_translator.translate(sql)
        assert result.command == "FT.AGGREGATE"
        assert "APPLY" in result.args


class TestGeoDistanceOperators:
    """Tests for all geo_distance operators (>, >=, <=, BETWEEN)."""

    def test_geo_distance_greater_than_uses_aggregate(self, geo_translator, geo_index):
        """geo_distance > radius should use FT.AGGREGATE with FILTER."""
        sql = f"SELECT name FROM {geo_index} WHERE geo_distance(location, POINT(-122.4, 37.8)) > 5000"
        result = geo_translator.translate(sql)
        assert result.command == "FT.AGGREGATE"
        assert "FILTER" in result.args

    def test_geo_distance_greater_equal_uses_aggregate(self, geo_translator, geo_index):
        """geo_distance >= radius should use FT.AGGREGATE with FILTER."""
        sql = f"SELECT name FROM {geo_index} WHERE geo_distance(location, POINT(-122.4, 37.8)) >= 5000"
        result = geo_translator.translate(sql)
        assert result.command == "FT.AGGREGATE"
        assert "FILTER" in result.args

    def test_geo_distance_less_equal_uses_search(self, geo_translator, geo_index):
        """geo_distance <= radius should use FT.SEARCH with GEOFILTER."""
        sql = f"SELECT name FROM {geo_index} WHERE geo_distance(location, POINT(-122.4, 37.8)) <= 5000"
        result = geo_translator.translate(sql)
        assert result.command == "FT.SEARCH"
        assert "GEOFILTER" in result.args

    def test_geo_distance_between_uses_aggregate(self, geo_translator, geo_index):
        """geo_distance BETWEEN x AND y should use FT.AGGREGATE with FILTER."""
        sql = f"SELECT name FROM {geo_index} WHERE geo_distance(location, POINT(-122.4, 37.8)) BETWEEN 1000 AND 5000"
        result = geo_translator.translate(sql)
        assert result.command == "FT.AGGREGATE"
        assert "FILTER" in result.args


class TestGeoValidation:
    """Tests for geo_distance validation and error handling."""

    def test_invalid_unit_raises_error(self, geo_translator, geo_index):
        """Invalid unit should raise ValueError."""
        sql = f"SELECT name FROM {geo_index} WHERE geo_distance(location, POINT(-122.4, 37.8), 'invalid') < 5000"
        with pytest.raises(ValueError, match="Unsupported geo distance unit"):
            geo_translator.translate(sql)

    def test_uppercase_unit_is_normalized(self, geo_translator, geo_index):
        """Uppercase units should be normalized to lowercase."""
        sql = f"SELECT name FROM {geo_index} WHERE geo_distance(location, POINT(-122.4, 37.8), 'KM') < 5"
        result = geo_translator.translate(sql)
        # Should work without error
        assert result.command == "FT.SEARCH"

    def test_geo_in_select_with_filter_applies_both(self, geo_translator, geo_index):
        """geo_distance in SELECT with < filter should apply filter in AGGREGATE."""
        sql = f"SELECT name, geo_distance(location, POINT(-122.4, 37.8)) AS dist FROM {geo_index} WHERE geo_distance(location, POINT(-122.4, 37.8)) < 5000"
        result = geo_translator.translate(sql)
        # Should use AGGREGATE (because of SELECT geo_distance)
        assert result.command == "FT.AGGREGATE"
        # Should have FILTER for the < condition
        assert "FILTER" in result.args


class TestGeoIntegration:
    """Integration tests verifying actual Redis execution."""

    @pytest.fixture
    def geo_data(self, redis_client, geo_index):
        """Populate geo index with test store locations."""
        stores = [
            {
                "name": "SF Downtown",
                "category": "retail",
                "location": "-122.4194,37.7749",
            },
            {
                "name": "NYC Times Square",
                "category": "retail",
                "location": "-73.9857,40.7580",
            },
        ]
        for i, store in enumerate(stores):
            redis_client.hset(f"store:{i+1}", mapping=store)
        return geo_index

    def test_raw_geofilter_works(self, redis_client, geo_data):
        """Verify raw GEOFILTER command works with Redis."""
        result = redis_client.execute_command(
            "FT.SEARCH",
            "test_geo_stores",
            "*",
            "GEOFILTER",
            "location",
            "-122.4194",
            "37.7749",
            "50",
            "km",
        )
        # Should return SF Downtown (within 50km of SF)
        assert result[0] >= 1  # At least one result
