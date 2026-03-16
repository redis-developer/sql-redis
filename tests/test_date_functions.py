"""Tests for DATE function parsing and translation (Phase 2 & 3)."""

import pytest

from sql_redis.parser import SQLParser


class TestDateFunctionParsing:
    """Tests for parsing date functions in SELECT clause."""

    @pytest.fixture
    def parser(self):
        """Create a SQLParser instance."""
        return SQLParser()

    def test_year_function_parsed(self, parser):
        """YEAR(field) should be parsed as a date function."""
        result = parser.parse("SELECT YEAR(created_at) AS year FROM events")

        assert len(result.date_functions) == 1
        assert result.date_functions[0].function == "YEAR"
        assert result.date_functions[0].field == "created_at"
        assert result.date_functions[0].alias == "year"

    def test_month_function_parsed(self, parser):
        """MONTH(field) should be parsed as a date function."""
        result = parser.parse("SELECT MONTH(created_at) AS month FROM events")

        assert len(result.date_functions) == 1
        assert result.date_functions[0].function == "MONTH"
        assert result.date_functions[0].field == "created_at"

    def test_day_function_parsed(self, parser):
        """DAY(field) should be parsed as a date function."""
        result = parser.parse("SELECT DAY(created_at) AS day FROM events")

        assert len(result.date_functions) == 1
        assert result.date_functions[0].function == "DAY"

    def test_multiple_date_functions(self, parser):
        """Multiple date functions should all be parsed."""
        result = parser.parse(
            "SELECT YEAR(created_at) AS year, MONTH(created_at) AS month FROM events"
        )

        assert len(result.date_functions) == 2
        funcs = {df.function for df in result.date_functions}
        assert funcs == {"YEAR", "MONTH"}

    def test_date_format_function_parsed(self, parser):
        """DATE_FORMAT(field, format) should be parsed with format string."""
        result = parser.parse(
            "SELECT DATE_FORMAT(created_at, '%Y-%m-%d') AS date FROM events"
        )

        assert len(result.date_functions) == 1
        assert result.date_functions[0].function == "DATE_FORMAT"
        assert result.date_functions[0].field == "created_at"
        assert result.date_functions[0].format_string == "%Y-%m-%d"

    def test_default_alias_generated(self, parser):
        """Date function without alias should get default alias."""
        result = parser.parse("SELECT YEAR(created_at) FROM events")

        assert len(result.date_functions) == 1
        assert result.date_functions[0].alias == "year_created_at"


class TestDateFunctionConditions:
    """Tests for date functions in WHERE clause."""

    @pytest.fixture
    def parser(self):
        """Create a SQLParser instance."""
        return SQLParser()

    def test_year_equals_condition(self, parser):
        """WHERE YEAR(field) = value should create YEAR_= condition."""
        result = parser.parse("SELECT * FROM events WHERE YEAR(created_at) = 2024")

        assert len(result.conditions) == 1
        assert result.conditions[0].field == "created_at"
        assert result.conditions[0].operator == "YEAR_="
        assert result.conditions[0].value == 2024

    def test_month_greater_than(self, parser):
        """WHERE MONTH(field) > value should create MONTH_> condition."""
        result = parser.parse("SELECT * FROM events WHERE MONTH(created_at) > 6")

        assert len(result.conditions) == 1
        assert result.conditions[0].operator == "MONTH_>"
        assert result.conditions[0].value == 6

    def test_combined_date_conditions(self, parser):
        """Multiple date function conditions should all be parsed."""
        result = parser.parse(
            "SELECT * FROM events WHERE YEAR(created_at) = 2024 AND MONTH(created_at) = 1"
        )

        assert len(result.conditions) == 2
        operators = {c.operator for c in result.conditions}
        assert operators == {"YEAR_=", "MONTH_="}


class TestDateFunctionTranslation:
    """Tests for translating date functions to Redis commands."""

    @pytest.fixture
    def date_index(self, redis_client):
        """Create an index with NUMERIC field for dates."""
        import redis as redis_lib

        index_name = "test_date_funcs"
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
        from sql_redis.schema import SchemaRegistry
        from sql_redis.translator import Translator

        registry = SchemaRegistry(redis_client)
        registry.load_all()
        return Translator(registry)

    def test_year_in_select_generates_apply(self, date_translator, date_index):
        """YEAR(field) in SELECT should generate APPLY year(@field)."""
        result = date_translator.translate(
            f"SELECT YEAR(created_at) AS year FROM {date_index}"
        )

        assert result.command == "FT.AGGREGATE"
        assert "APPLY" in result.args
        apply_idx = result.args.index("APPLY")
        assert "year(@created_at)" in result.args[apply_idx + 1]
        assert result.args[apply_idx + 3] == "year"

    def test_date_format_generates_timefmt(self, date_translator, date_index):
        """DATE_FORMAT should generate timefmt() expression."""
        result = date_translator.translate(
            f"SELECT DATE_FORMAT(created_at, '%Y-%m-%d') AS date FROM {date_index}"
        )

        assert result.command == "FT.AGGREGATE"
        assert "APPLY" in result.args
        # Should contain timefmt with format string
        apply_idx = result.args.index("APPLY")
        assert "timefmt" in result.args[apply_idx + 1]
        assert "%Y-%m-%d" in result.args[apply_idx + 1]

    def test_year_condition_generates_filter(self, date_translator, date_index):
        """WHERE YEAR(field) = value should generate APPLY + FILTER."""
        result = date_translator.translate(
            f"SELECT * FROM {date_index} WHERE YEAR(created_at) = 2024"
        )

        assert result.command == "FT.AGGREGATE"
        # Should have APPLY for year computation
        assert "APPLY" in result.args
        # Should have FILTER for the condition
        assert "FILTER" in result.args
        filter_idx = result.args.index("FILTER")
        assert "@year_created_at == 2024" in result.args[filter_idx + 1]

    def test_combined_date_function_filter(self, date_translator, date_index):
        """Multiple date conditions should all generate FILTER expressions."""
        result = date_translator.translate(
            f"SELECT * FROM {date_index} "
            f"WHERE YEAR(created_at) = 2024 AND MONTH(created_at) >= 6"
        )

        assert result.command == "FT.AGGREGATE"
        # Should have two FILTER expressions
        filter_count = result.args.count("FILTER")
        assert filter_count == 2

    def test_group_by_year_month(self, date_translator, date_index):
        """GROUP BY with date functions should work."""
        result = date_translator.translate(
            f"SELECT YEAR(created_at) AS year, COUNT(*) AS cnt FROM {date_index} "
            f"GROUP BY year"
        )

        assert result.command == "FT.AGGREGATE"
        assert "GROUPBY" in result.args
        assert "REDUCE" in result.args
        assert "COUNT" in result.args

    def test_negated_date_function_raises_error(self, date_translator, date_index):
        """NOT YEAR(...) should raise a clear error."""
        sql = f"SELECT * FROM {date_index} WHERE NOT YEAR(created_at) = 2024"
        with pytest.raises(ValueError, match="Negated date function"):
            date_translator.translate(sql)

    def test_date_format_in_where_raises_error(self, date_translator, date_index):
        """DATE_FORMAT in WHERE should raise a clear error."""
        sql = f"SELECT * FROM {date_index} WHERE DATE_FORMAT(created_at, '%Y-%m') = '2024-01'"
        with pytest.raises(ValueError, match="DATE_FORMAT in WHERE"):
            date_translator.translate(sql)

    def test_custom_alias_with_where_filter(self, date_translator, date_index):
        """SELECT with custom alias + WHERE should compute canonical alias for FILTER."""
        result = date_translator.translate(
            f"SELECT YEAR(created_at) AS year FROM {date_index} "
            f"WHERE YEAR(created_at) = 2024"
        )
        # Should have APPLY for both: custom alias 'year' AND canonical 'year_created_at'
        assert result.command == "FT.AGGREGATE"
        apply_indices = [i for i, arg in enumerate(result.args) if arg == "APPLY"]
        # Should have at least 2 APPLYs (one for SELECT, one for FILTER)
        assert len(apply_indices) >= 2
        # FILTER should reference the canonical alias
        assert "FILTER" in result.args
        filter_idx = result.args.index("FILTER")
        assert "year_created_at" in result.args[filter_idx + 1]
