"""Tests for the SQL to Redis Translator."""

import pytest
import redis

from sql_redis.schema import SchemaRegistry
from sql_redis.translator import Translator


@pytest.fixture
def basic_index(redis_client: redis.Redis) -> str:
    """Create a basic test index."""
    index_name = "test_basic"
    # Drop if exists
    try:
        redis_client.execute_command("FT.DROPINDEX", index_name, "DD")
    except redis.ResponseError:
        pass
    # Create index
    redis_client.execute_command(
        "FT.CREATE",
        index_name,
        "ON",
        "HASH",
        "PREFIX",
        "1",
        "doc:",
        "SCHEMA",
        "title",
        "TEXT",
        "content",
        "TEXT",
        "category",
        "TAG",
        "price",
        "NUMERIC",
        "status",
        "TAG",
    )
    return index_name


@pytest.fixture
def geo_index(redis_client: redis.Redis) -> str:
    """Create an index with GEO field."""
    index_name = "test_geo"
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
        "location",
        "GEO",
    )
    return index_name


@pytest.fixture
def vector_index(redis_client: redis.Redis) -> str:
    """Create an index with VECTOR field."""
    index_name = "test_vector"
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
        "vec:",
        "SCHEMA",
        "title",
        "TEXT",
        "embedding",
        "VECTOR",
        "HNSW",
        "6",
        "TYPE",
        "FLOAT32",
        "DIM",
        "128",
        "DISTANCE_METRIC",
        "COSINE",
    )
    return index_name


@pytest.fixture
def translator(redis_client: redis.Redis, basic_index: str) -> Translator:
    """Create a translator with the basic index loaded."""
    registry = SchemaRegistry(redis_client)
    registry.load_all()
    return Translator(registry)


@pytest.fixture
def full_translator(
    redis_client: redis.Redis, basic_index: str, geo_index: str, vector_index: str
) -> Translator:
    """Create a translator with all test indexes loaded."""
    registry = SchemaRegistry(redis_client)
    registry.load_all()
    return Translator(registry)


class TestTranslatorBasicSearch:
    """Tests for basic FT.SEARCH translation."""

    def test_select_all(self, translator: Translator, basic_index: str):
        """SELECT * FROM index -> FT.SEARCH index *"""
        result = translator.translate(f"SELECT * FROM {basic_index}")

        assert result.command == "FT.SEARCH"
        assert result.index == basic_index
        assert result.query_string == "*"

    def test_select_with_text_filter(self, translator: Translator, basic_index: str):
        """SELECT with TEXT field condition."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE title = 'hello'"
        )

        assert result.command == "FT.SEARCH"
        assert result.query_string == "@title:hello"

    def test_select_with_numeric_filter(self, translator: Translator, basic_index: str):
        """SELECT with NUMERIC field condition."""
        result = translator.translate(f"SELECT * FROM {basic_index} WHERE price > 100")

        assert result.command == "FT.SEARCH"
        assert result.query_string == "@price:[(100 +inf]"

    def test_select_with_tag_filter(self, translator: Translator, basic_index: str):
        """SELECT with TAG field condition."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE category = 'electronics'"
        )

        assert result.command == "FT.SEARCH"
        assert result.query_string == "@category:{electronics}"

    def test_select_specific_fields(self, translator: Translator, basic_index: str):
        """SELECT specific fields adds RETURN clause."""
        result = translator.translate(f"SELECT title, price FROM {basic_index}")

        assert "RETURN" in result.args
        idx = result.args.index("RETURN")
        assert result.args[idx + 1] == "2"
        assert "title" in result.args
        assert "price" in result.args

    def test_select_with_limit(self, translator: Translator, basic_index: str):
        """SELECT with LIMIT adds LIMIT args."""
        result = translator.translate(f"SELECT * FROM {basic_index} LIMIT 10")

        assert "LIMIT" in result.args
        idx = result.args.index("LIMIT")
        assert result.args[idx + 1] == "0"  # offset
        assert result.args[idx + 2] == "10"  # count

    def test_select_with_limit_offset(self, translator: Translator, basic_index: str):
        """SELECT with LIMIT and OFFSET."""
        result = translator.translate(f"SELECT * FROM {basic_index} LIMIT 10 OFFSET 20")

        idx = result.args.index("LIMIT")
        assert result.args[idx + 1] == "20"  # offset
        assert result.args[idx + 2] == "10"  # count

    def test_select_with_order_by(self, translator: Translator, basic_index: str):
        """SELECT with ORDER BY adds SORTBY."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} ORDER BY price DESC"
        )

        assert "SORTBY" in result.args
        idx = result.args.index("SORTBY")
        assert result.args[idx + 1] == "price"
        assert result.args[idx + 2] == "DESC"


class TestTranslatorBooleanConditions:
    """Tests for boolean condition combinations."""

    def test_and_conditions(self, translator: Translator, basic_index: str):
        """Multiple conditions with AND."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE title = 'hello' AND price > 50"
        )

        assert "@title:hello" in result.query_string
        assert "@price:[(50 +inf]" in result.query_string

    def test_or_conditions(self, translator: Translator, basic_index: str):
        """Multiple conditions with OR."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE category = 'a' OR category = 'b'"
        )

        assert "|" in result.query_string  # OR uses pipe


class TestTranslatorAggregate:
    """Tests for FT.AGGREGATE translation."""

    def test_count_aggregation(self, translator: Translator, basic_index: str):
        """SELECT COUNT(*) generates FT.AGGREGATE."""
        result = translator.translate(f"SELECT COUNT(*) FROM {basic_index}")

        assert result.command == "FT.AGGREGATE"
        assert "GROUPBY" in result.args
        assert "REDUCE" in result.args
        assert "COUNT" in result.args

    def test_count_with_alias(self, translator: Translator, basic_index: str):
        """SELECT COUNT(*) AS cnt includes alias."""
        result = translator.translate(f"SELECT COUNT(*) AS cnt FROM {basic_index}")

        assert "AS" in result.args
        assert "cnt" in result.args

    def test_sum_aggregation(self, translator: Translator, basic_index: str):
        """SELECT SUM(field) generates REDUCE SUM (global aggregation with field)."""
        result = translator.translate(f"SELECT SUM(price) AS total FROM {basic_index}")

        assert result.command == "FT.AGGREGATE"
        assert "SUM" in result.args
        assert "@price" in result.args
        # Global aggregation has GROUPBY 0
        assert "GROUPBY" in result.args
        idx = result.args.index("GROUPBY")
        assert result.args[idx + 1] == "0"

    def test_group_by(self, translator: Translator, basic_index: str):
        """SELECT with GROUP BY."""
        result = translator.translate(
            f"SELECT category, COUNT(*) FROM {basic_index} GROUP BY category"
        )

        assert result.command == "FT.AGGREGATE"
        idx = result.args.index("GROUPBY")
        assert result.args[idx + 1] == "1"
        assert "@category" in result.args

    def test_group_by_with_sum(self, translator: Translator, basic_index: str):
        """GROUP BY with SUM aggregation (has field)."""
        result = translator.translate(
            f"SELECT category, SUM(price) AS total FROM {basic_index} GROUP BY category"
        )

        assert result.command == "FT.AGGREGATE"
        assert "SUM" in result.args
        assert "@price" in result.args

    def test_aggregate_with_order_by(self, translator: Translator, basic_index: str):
        """FT.AGGREGATE with SORTBY."""
        result = translator.translate(
            f"SELECT category, COUNT(*) AS cnt FROM {basic_index} "
            f"GROUP BY category ORDER BY cnt DESC"
        )

        assert "SORTBY" in result.args

    def test_aggregate_with_limit(self, translator: Translator, basic_index: str):
        """FT.AGGREGATE with LIMIT."""
        result = translator.translate(f"SELECT COUNT(*) FROM {basic_index} LIMIT 5")

        assert "LIMIT" in result.args

    def test_computed_field(self, translator: Translator, basic_index: str):
        """Computed field generates APPLY."""
        result = translator.translate(
            f"SELECT price * 2 AS double_price FROM {basic_index}"
        )

        assert result.command == "FT.AGGREGATE"
        assert "APPLY" in result.args

    def test_count_with_field_uses_zero_args(
        self, translator: Translator, basic_index: str
    ):
        """COUNT(field) should generate REDUCE COUNT 0, not REDUCE COUNT 1 @field.

        Redis COUNT reducer always takes 0 arguments - it counts rows, not field values.
        """
        result = translator.translate(
            f"SELECT category, COUNT(price) AS count_price FROM {basic_index} GROUP BY category"
        )

        assert result.command == "FT.AGGREGATE"
        # Find REDUCE COUNT in args and verify it's followed by "0"
        args = result.args
        reduce_idx = args.index("REDUCE")
        assert args[reduce_idx + 1] == "COUNT"
        assert args[reduce_idx + 2] == "0"  # COUNT always takes 0 args
        # Should NOT have @price after COUNT
        assert "@price" not in args[reduce_idx + 2 : reduce_idx + 4]

    def test_count_star_uses_zero_args(self, translator: Translator, basic_index: str):
        """COUNT(*) should generate REDUCE COUNT 0."""
        result = translator.translate(
            f"SELECT category, COUNT(*) AS cnt FROM {basic_index} GROUP BY category"
        )

        args = result.args
        reduce_idx = args.index("REDUCE")
        assert args[reduce_idx + 1] == "COUNT"
        assert args[reduce_idx + 2] == "0"

    def test_count_distinct_reducer(self, translator: Translator, basic_index: str):
        """COUNT_DISTINCT(field) should generate REDUCE COUNT_DISTINCT 1 @field."""
        result = translator.translate(
            f"SELECT category, COUNT_DISTINCT(title) AS unique_titles "
            f"FROM {basic_index} GROUP BY category"
        )

        assert result.command == "FT.AGGREGATE"
        args = result.args
        reduce_idx = args.index("REDUCE")
        assert args[reduce_idx + 1] == "COUNT_DISTINCT"
        assert args[reduce_idx + 2] == "1"
        assert args[reduce_idx + 3] == "@title"
        assert "AS" in args
        assert "unique_titles" in args

    def test_quantile_reducer(self, translator: Translator, basic_index: str):
        """QUANTILE(field, value) should generate REDUCE QUANTILE 2 @field value."""
        result = translator.translate(
            f"SELECT category, QUANTILE(price, 0.5) AS median_price "
            f"FROM {basic_index} GROUP BY category"
        )

        assert result.command == "FT.AGGREGATE"
        args = result.args
        reduce_idx = args.index("REDUCE")
        assert args[reduce_idx + 1] == "QUANTILE"
        assert args[reduce_idx + 2] == "2"  # nargs = 1 (field) + 1 (quantile value)
        assert args[reduce_idx + 3] == "@price"
        assert args[reduce_idx + 4] == "0.5"
        assert "AS" in args
        assert "median_price" in args


class TestTranslatorVectorSearch:
    """Tests for vector search translation."""

    def test_vector_search_simple(self, full_translator: Translator, vector_index: str):
        """Vector search generates KNN syntax."""
        result = full_translator.translate(
            f"SELECT title, vector_distance(embedding, :vec) AS score "
            f"FROM {vector_index} LIMIT 5"
        )

        assert result.command == "FT.SEARCH"
        assert "KNN" in result.query_string
        assert "@embedding" in result.query_string
        assert "DIALECT" in result.args

    def test_vector_search_with_prefilter(
        self, full_translator: Translator, vector_index: str
    ):
        """Vector search with prefilter condition."""
        result = full_translator.translate(
            f"SELECT title, vector_distance(embedding, :vec) AS score "
            f"FROM {vector_index} WHERE title = 'hello' LIMIT 5"
        )

        assert "=>" in result.query_string  # Prefilter syntax


class TestTranslatorUnknownField:
    """Tests for unknown field type handling."""

    def test_unknown_field_defaults_to_text(
        self, full_translator: Translator, geo_index: str
    ):
        """Unknown field type defaults to text search."""
        # GEO field used in equality condition - treated as text
        result = full_translator.translate(
            f"SELECT * FROM {geo_index} WHERE location = 'test'"
        )

        assert "@location" in result.query_string


class TestTranslatorNegation:
    """Tests for negated conditions."""

    def test_not_equal_text(self, translator: Translator, basic_index: str):
        """NOT condition on TEXT field."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE title != 'bad'"
        )

        assert "-@title" in result.query_string

    def test_not_condition(self, translator: Translator, basic_index: str):
        """NOT prefix negation."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE NOT title = 'bad'"
        )

        assert "-@title" in result.query_string


class TestTranslatorOutput:
    """Tests for output format methods."""

    def test_to_command_list(self, translator: Translator, basic_index: str):
        """to_command_list() returns list for execute_command."""
        result = translator.translate(f"SELECT * FROM {basic_index}")
        cmd_list = result.to_command_list()

        assert cmd_list[0] == "FT.SEARCH"
        assert cmd_list[1] == basic_index
        assert cmd_list[2] == "*"

    def test_to_command_string(self, translator: Translator, basic_index: str):
        """to_command_string() returns human-readable string."""
        result = translator.translate(f"SELECT * FROM {basic_index}")
        cmd_str = result.to_command_string()

        assert cmd_str.startswith("FT.SEARCH")
        assert basic_index in cmd_str



class TestTranslatorDialect2:
    """Tests for unconditional DIALECT 2 in all commands."""

    def test_search_includes_dialect_2(self, translator: Translator, basic_index: str):
        """Every FT.SEARCH command ends with DIALECT 2."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE status = 'active'"
        )
        assert result.args[-2:] == ["DIALECT", "2"]

    def test_aggregate_includes_dialect_2(
        self, translator: Translator, basic_index: str
    ):
        """Every FT.AGGREGATE command ends with DIALECT 2."""
        result = translator.translate(f"SELECT COUNT(*) FROM {basic_index}")
        assert result.args[-2:] == ["DIALECT", "2"]

    def test_search_select_all_includes_dialect_2(
        self, translator: Translator, basic_index: str
    ):
        """Even SELECT * includes DIALECT 2."""
        result = translator.translate(f"SELECT * FROM {basic_index}")
        assert result.args[-2:] == ["DIALECT", "2"]


class TestTranslatorIsMissing:
    """Tests for IS NULL / IS NOT NULL → ismissing() translation."""

    def test_is_null_produces_ismissing(self, translator: Translator, basic_index: str):
        """WHERE field IS NULL → ismissing(@field)."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE status IS NULL"
        )
        assert result.command == "FT.SEARCH"
        assert result.query_string == "ismissing(@status)"

    def test_is_not_null_produces_neg_ismissing(
        self, translator: Translator, basic_index: str
    ):
        """WHERE field IS NOT NULL → -ismissing(@field)."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE status IS NOT NULL"
        )
        assert result.command == "FT.SEARCH"
        assert result.query_string == "-ismissing(@status)"

    def test_is_null_combined_with_other_conditions(
        self, translator: Translator, basic_index: str
    ):
        """IS NULL combined with a regular TAG condition."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE category = 'electronics' AND title IS NULL"
        )
        assert result.command == "FT.SEARCH"
        assert "ismissing(@title)" in result.query_string
        assert "@category" in result.query_string

    def test_is_null_emits_warning(self, translator: Translator, basic_index: str):
        """IS NULL translation emits a warning about Redis version requirement."""
        import warnings

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            translator.translate(f"SELECT * FROM {basic_index} WHERE status IS NULL")
            assert len(w) == 1
            assert "Redis 7.4+" in str(w[0].message)
            assert "INDEXMISSING" in str(w[0].message)

    def test_is_not_null_emits_warning(self, translator: Translator, basic_index: str):
        """IS NOT NULL translation emits a warning about Redis version requirement."""
        import warnings

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            translator.translate(
                f"SELECT * FROM {basic_index} WHERE status IS NOT NULL"
            )
            assert len(w) == 1
            assert "Redis 7.4+" in str(w[0].message)
