"""Tests for DATE/DATETIME literal parsing and conversion."""

import pytest
import redis as redis_lib

from sql_redis.parser import SQLParser
from sql_redis.schema import SchemaRegistry
from sql_redis.translator import Translator


class TestDateLiteralParsing:
    """Tests for parsing date literals in SQL queries.

    Note: Date conversion is deferred to the translator (where field types are known).
    The parser preserves original string values to avoid changing semantics for
    TEXT/TAG fields that may contain date-like strings.
    """

    @pytest.fixture
    def parser(self):
        """Create a SQLParser instance."""
        return SQLParser()

    def test_date_literal_preserved_as_string(self, parser):
        """Date literal '2024-01-01' should be preserved as string in parser."""
        result = parser.parse("SELECT * FROM events WHERE created_at > '2024-01-01'")

        assert len(result.conditions) == 1
        # Parser preserves string; translator converts for NUMERIC fields
        assert result.conditions[0].value == "2024-01-01"
        assert result.conditions[0].field == "created_at"
        assert result.conditions[0].operator == ">"

    def test_datetime_literal_preserved(self, parser):
        """Datetime literal should be preserved as string."""
        result = parser.parse(
            "SELECT * FROM events WHERE created_at = '2024-01-01T12:00:00'"
        )

        assert len(result.conditions) == 1
        assert result.conditions[0].value == "2024-01-01T12:00:00"

    def test_datetime_literal_with_space_preserved(self, parser):
        """Datetime literal '2024-01-01 12:00:00' should be preserved."""
        result = parser.parse(
            "SELECT * FROM events WHERE created_at = '2024-01-01 12:00:00'"
        )

        assert len(result.conditions) == 1
        assert result.conditions[0].value == "2024-01-01 12:00:00"

    def test_datetime_literal_with_z_suffix_preserved(self, parser):
        """Datetime literal with Z suffix should be preserved."""
        result = parser.parse(
            "SELECT * FROM events WHERE created_at = '2024-01-01T12:00:00Z'"
        )

        assert len(result.conditions) == 1
        assert result.conditions[0].value == "2024-01-01T12:00:00Z"

    def test_datetime_literal_with_timezone_offset_preserved(self, parser):
        """Datetime literal with timezone offset should be preserved."""
        result = parser.parse(
            "SELECT * FROM events WHERE created_at = '2024-01-01T12:00:00+00:00'"
        )

        assert len(result.conditions) == 1
        assert result.conditions[0].value == "2024-01-01T12:00:00+00:00"

    def test_datetime_literal_with_fractional_seconds_preserved(self, parser):
        """Datetime literal with fractional seconds should be preserved."""
        result = parser.parse(
            "SELECT * FROM events WHERE created_at = '2024-01-01T12:00:00.123Z'"
        )

        assert len(result.conditions) == 1
        assert result.conditions[0].value == "2024-01-01T12:00:00.123Z"

    def test_datetime_literal_with_timezone_no_colon_preserved(self, parser):
        """Datetime literal with +0000 format should be preserved."""
        result = parser.parse(
            "SELECT * FROM events WHERE created_at = '2024-01-01T12:00:00+0000'"
        )

        assert len(result.conditions) == 1
        assert result.conditions[0].value == "2024-01-01T12:00:00+0000"

    def test_date_between_range_preserved(self, parser):
        """BETWEEN with date literals should preserve both bounds as strings."""
        result = parser.parse(
            "SELECT * FROM events WHERE created_at BETWEEN '2024-01-01' AND '2024-01-31'"
        )

        assert len(result.conditions) == 1
        assert result.conditions[0].operator == "BETWEEN"
        low, high = result.conditions[0].value
        assert low == "2024-01-01"
        assert high == "2024-01-31"

    def test_non_date_string_not_converted(self, parser):
        """Regular strings should not be converted to timestamps."""
        result = parser.parse("SELECT * FROM users WHERE name = 'John'")

        assert len(result.conditions) == 1
        assert result.conditions[0].value == "John"

    def test_numeric_value_unchanged(self, parser):
        """Numeric values should remain unchanged."""
        result = parser.parse("SELECT * FROM events WHERE created_at > 1704067200")

        assert len(result.conditions) == 1
        assert result.conditions[0].value == 1704067200

    def test_multiple_date_conditions_preserved(self, parser):
        """Multiple date conditions should preserve strings."""
        result = parser.parse(
            "SELECT * FROM events WHERE created_at > '2024-01-01' AND created_at < '2024-12-31'"
        )

        assert len(result.conditions) == 2
        assert result.conditions[0].value == "2024-01-01"
        assert result.conditions[1].value == "2024-12-31"


class TestDateTranslation:
    """Tests for translating date queries to Redis commands."""

    @pytest.fixture
    def date_index(self, redis_client):
        """Create an index with NUMERIC field for dates."""
        index_name = "test_dates"
        try:
            redis_client.execute_command("FT.DROPINDEX", index_name, "DD")
        except redis_lib.ResponseError:
            pass
        redis_client.execute_command(
            "FT.CREATE",
            index_name,
            "ON",
            "HASH",
            "PREFIX",
            "1",
            "event:",
            "SCHEMA",
            "created_at",
            "NUMERIC",
            "SORTABLE",
            "name",
            "TEXT",
        )
        return index_name

    @pytest.fixture
    def date_translator(self, redis_client, date_index):
        """Create a translator with the date index loaded."""
        registry = SchemaRegistry(redis_client)
        registry.load_all()
        return Translator(registry)

    def test_date_query_generates_numeric_filter(self, date_translator, date_index):
        """Date query should generate NUMERIC filter with timestamp."""
        result = date_translator.translate(
            f"SELECT * FROM {date_index} WHERE created_at > '2024-01-01'"
        )

        assert result.command == "FT.SEARCH"
        # Should contain numeric filter with timestamp
        assert "@created_at:[(1704067200 +inf]" in result.query_string

    def test_date_between_generates_numeric_range(self, date_translator, date_index):
        """BETWEEN with dates should generate numeric range filter."""
        result = date_translator.translate(
            f"SELECT * FROM {date_index} WHERE created_at BETWEEN '2024-01-01' AND '2024-01-31'"
        )

        assert result.command == "FT.SEARCH"
        # Should contain numeric range
        assert "@created_at:[1704067200 1706659200]" in result.query_string
