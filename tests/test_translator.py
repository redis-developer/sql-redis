"""Tests for the SQL to Redis Translator."""

import warnings

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

    def test_boolean_in_numeric_context_raises(
        self, translator: Translator, basic_index: str
    ):
        """WHERE price = true should raise, not produce @price:[True True]."""
        with pytest.raises(ValueError, match="Boolean value"):
            translator.translate(f"SELECT * FROM {basic_index} WHERE price = true")


class TestTranslatorMixedBooleanLogic:
    """Tests that mixed AND/OR WHERE clauses keep their SQL grouping.

    Regression coverage for the bug where ``A AND (B OR C)`` and similar
    expressions were flattened to a single boolean operator (e.g.
    ``@a|@b|@c``), losing the user's intended precedence.
    """

    def test_and_with_nested_or(self, translator: Translator, basic_index: str):
        """A AND (B OR C) -> ``@a (B|C)`` and the OR group is parenthesized."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} "
            "WHERE category = 'a' AND (status = 'b' OR status = 'c')"
        )

        assert result.query_string == "@category:{a} (@status:{b}|@status:{c})"

    def test_or_with_nested_and(self, translator: Translator, basic_index: str):
        """A OR (B AND C) -> ``(@a|@b @c)`` with the whole tree wrapped."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} "
            "WHERE category = 'a' OR (status = 'b' AND price > 50)"
        )

        assert result.query_string == "(@category:{a}|@status:{b} @price:[(50 +inf])"

    def test_or_group_first_then_and(self, translator: Translator, basic_index: str):
        """(B OR C) AND A -> ``(@b|@c) @a`` keeps the leading OR group."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} "
            "WHERE (status = 'b' OR status = 'c') AND category = 'a'"
        )

        assert result.query_string == "(@status:{b}|@status:{c}) @category:{a}"

    def test_chained_ands_with_trailing_or_group(
        self, translator: Translator, basic_index: str
    ):
        """A AND B AND C AND (D OR E) keeps the OR group only around D|E."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} "
            "WHERE category = 'a' AND status = 'b' AND price > 10 "
            "AND (title = 'd' OR title = 'e')"
        )

        assert (
            result.query_string
            == "@category:{a} @status:{b} @price:[(10 +inf] (@title:d|@title:e)"
        )

    def test_two_or_groups_anded(self, translator: Translator, basic_index: str):
        """(A OR B) AND (C OR D) keeps both OR groups parenthesized."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} "
            "WHERE (category = 'a' OR category = 'b') "
            "AND (status = 'c' OR status = 'd')"
        )

        assert (
            result.query_string
            == "(@category:{a}|@category:{b}) (@status:{c}|@status:{d})"
        )

    def test_pure_and_chain_unchanged(self, translator: Translator, basic_index: str):
        """A AND B AND C still renders as space-joined without parens."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE category = 'a' "
            "AND status = 'b' AND price > 10"
        )

        assert result.query_string == "@category:{a} @status:{b} @price:[(10 +inf]"

    def test_pure_or_chain_unchanged(self, translator: Translator, basic_index: str):
        """A OR B OR C still renders as a single pipe-joined OR group."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} "
            "WHERE category = 'a' OR category = 'b' OR category = 'c'"
        )

        assert result.query_string == "(@category:{a}|@category:{b}|@category:{c})"


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

    def test_sql_count_distinct_routes_to_count_distinct(
        self, translator: Translator, basic_index: str
    ):
        """SQL COUNT(DISTINCT x) emits REDUCE COUNT_DISTINCT 1 @x, not COUNT 0."""
        result = translator.translate(
            f"SELECT category, COUNT(DISTINCT title) AS unique_titles "
            f"FROM {basic_index} GROUP BY category"
        )

        assert result.command == "FT.AGGREGATE"
        args = result.args
        reduce_idx = args.index("REDUCE")
        assert args[reduce_idx + 1] == "COUNT_DISTINCT"
        assert args[reduce_idx + 2] == "1"
        assert args[reduce_idx + 3] == "@title"
        assert args[reduce_idx + 4] == "AS"
        assert args[reduce_idx + 5] == "unique_titles"

    def test_sql_count_distinct_global_aggregation(
        self, translator: Translator, basic_index: str
    ):
        """Global COUNT(DISTINCT x) (no GROUP BY) still emits COUNT_DISTINCT."""
        result = translator.translate(
            f"SELECT COUNT(DISTINCT title) AS n FROM {basic_index}"
        )

        assert result.command == "FT.AGGREGATE"
        args = result.args
        # GROUPBY 0 for global aggregation
        groupby_idx = args.index("GROUPBY")
        assert args[groupby_idx + 1] == "0"
        reduce_idx = args.index("REDUCE")
        assert args[reduce_idx + 1] == "COUNT_DISTINCT"
        assert args[reduce_idx + 2] == "1"
        assert args[reduce_idx + 3] == "@title"

    def test_sql_count_distinct_matches_count_distinct_function(
        self, translator: Translator, basic_index: str
    ):
        """COUNT(DISTINCT x) and COUNT_DISTINCT(x) emit equivalent reducers."""
        sql_distinct = translator.translate(
            f"SELECT category, COUNT(DISTINCT title) AS n "
            f"FROM {basic_index} GROUP BY category"
        )
        redis_distinct = translator.translate(
            f"SELECT category, COUNT_DISTINCT(title) AS n "
            f"FROM {basic_index} GROUP BY category"
        )

        assert sql_distinct.args == redis_distinct.args

    def test_sql_sum_distinct_raises(self, translator: Translator, basic_index: str):
        """SUM(DISTINCT x) is rejected — no native RediSearch equivalent."""
        with pytest.raises(ValueError, match="DISTINCT"):
            translator.translate(f"SELECT SUM(DISTINCT price) FROM {basic_index}")

    def test_sql_count_distinct_multi_column_raises(
        self, translator: Translator, basic_index: str
    ):
        """COUNT(DISTINCT a, b) is rejected — multi-column DISTINCT unsupported."""
        with pytest.raises(ValueError, match="single column"):
            translator.translate(
                f"SELECT COUNT(DISTINCT title, category) FROM {basic_index}"
            )

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

    def test_double_negation_cancels(self, translator: Translator, basic_index: str):
        """NOT (field != x) double negation resolves to positive match."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE NOT title != 'good'"
        )

        assert result.query_string == "@title:good"


class TestTranslatorTrialUserBug:
    """Regression tests for the v0.5.0 trial-user bug report (error_spec.md).

    Critical:
    - NOT is silently dropped for >, >=, <, <=, BETWEEN, IN on tag/numeric.
    - TAG pipe | is not escaped, so 'a|b' is interpreted as 'a' OR 'b'.

    High:
    - IN on NUMERIC crashes.
    - LIKE '' produces invalid syntax.
    - BETWEEN on TAG produces invalid syntax.
    - Double-quoted value (identifier) becomes None.

    Medium:
    - SELECT DISTINCT is silently ignored.
    - Multi-column ORDER BY drops trailing keys.
    """

    def test_not_in_on_tag(self, translator: Translator, basic_index: str):
        """NOT IN on TAG → -@status:{a|b} (excludes), not @status:{a|b} (matches)."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE status NOT IN ('a', 'b')"
        )
        assert result.query_string == "-@status:{a|b}"

    def test_not_between_on_numeric(self, translator: Translator, basic_index: str):
        """NOT BETWEEN on NUMERIC → -@price:[10 20] (outside range)."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE price NOT BETWEEN 10 AND 20"
        )
        assert result.query_string == "-@price:[10 20]"

    def test_not_greater_than_on_numeric(
        self, translator: Translator, basic_index: str
    ):
        """NOT field > 5 on NUMERIC → -@price:[(5 +inf] (i.e. price <= 5)."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE NOT price > 5"
        )
        # Both shapes are semantically equivalent (NOT (price>5) == price<=5).
        # Accept either the literal -[(5 +inf] form or the rewritten [-inf 5] form.
        assert result.query_string in ("-@price:[(5 +inf]", "@price:[-inf 5]")

    def test_mixed_not_text_and_not_numeric(
        self, translator: Translator, basic_index: str
    ):
        """The crystallizing example: only the = negation worked before; > was dropped.

        NOT title = 'x' AND NOT price > 50 must negate both sides.
        """
        result = translator.translate(
            f"SELECT * FROM {basic_index} " "WHERE NOT title = 'x' AND NOT price > 50"
        )
        assert "-@title:x" in result.query_string
        # The numeric NOT must also be applied (was previously dropped).
        assert (
            "-@price:[(50 +inf]" in result.query_string
            or "@price:[-inf 50]" in result.query_string
        )

    def test_tag_value_with_pipe_is_escaped(
        self, translator: Translator, basic_index: str
    ):
        """status = 'a|b' must match the literal value 'a|b', not 'a' OR 'b'."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE status = 'a|b'"
        )
        assert result.query_string == r"@status:{a\|b}"

    def test_tag_in_with_pipe_inside_value(
        self, translator: Translator, basic_index: str
    ):
        """IN ('x|y', 'z') must yield two values (x|y, z), not three."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE status IN ('x|y', 'z')"
        )
        assert result.query_string == r"@status:{x\|y|z}"

    def test_in_on_numeric_does_not_crash(
        self, translator: Translator, basic_index: str
    ):
        """price IN (1, 2, 3) must not raise TypeError.

        Either a clear ValueError (rejecting IN on numeric) or a valid query
        such as the union of equality ranges is acceptable; the crash on a
        list-to-float coercion is not.
        """
        try:
            result = translator.translate(
                f"SELECT * FROM {basic_index} WHERE price IN (1, 2, 3)"
            )
        except ValueError:
            # Surfacing the limitation is acceptable
            return
        # If it succeeds, must not be the broken `[1 [1, 2, 3]]` shape.
        assert "[1, 2, 3]" not in result.query_string

    def test_empty_like_does_not_produce_bare_colon(
        self, translator: Translator, basic_index: str
    ):
        """LIKE '' must not emit a bare `@title:` (invalid RediSearch syntax).

        A clear ValueError is preferred over silent invalid output.
        """
        try:
            result = translator.translate(
                f"SELECT * FROM {basic_index} WHERE title LIKE ''"
            )
        except ValueError:
            return
        # If accepted, the query string must not contain the bare-colon shape.
        assert not result.query_string.rstrip().endswith("@title:")
        assert "@title: " not in result.query_string

    def test_between_on_tag_is_rejected(self, translator: Translator, basic_index: str):
        """BETWEEN on a TAG field is meaningless and must raise.

        Previously produced @status:{\\('a'\\, 'z'\\)}, which is invalid.
        """
        with pytest.raises(ValueError):
            translator.translate(
                f"SELECT * FROM {basic_index} " "WHERE status BETWEEN 'a' AND 'z'"
            )

    def test_double_quoted_value_does_not_become_none(
        self, translator: Translator, basic_index: str
    ):
        """status = "active" (double-quoted SQL identifier) must not become None.

        sqlglot parses "active" as an identifier (exp.Column). The translator
        previously called _extract_literal_value which returned None, yielding
        @status:{None}. Either accept it as a string literal or raise; never
        emit the literal token 'None' into the query.
        """
        try:
            result = translator.translate(
                f'SELECT * FROM {basic_index} WHERE status = "active"'
            )
        except ValueError:
            return
        assert "None" not in result.query_string
        # If accepted, expect the value treated as a string literal:
        assert "@status:{active}" in result.query_string

    def test_select_distinct_is_not_silently_ignored(
        self, translator: Translator, basic_index: str
    ):
        """SELECT DISTINCT must not silently behave like a plain SELECT.

        Either: produce a query that deduplicates (e.g. GROUPBY on the
        selected columns), or raise to surface that DISTINCT is unsupported.
        Silently returning duplicate rows is the bug.
        """
        try:
            result = translator.translate(f"SELECT DISTINCT status FROM {basic_index}")
        except ValueError:
            return
        # FT.SEARCH cannot dedupe; an FT.AGGREGATE with GROUPBY @status is the
        # only sane way to honor DISTINCT here.
        assert result.command == "FT.AGGREGATE"
        assert "GROUPBY" in result.args
        gb_idx = result.args.index("GROUPBY")
        # Must group by @status
        assert "@status" in result.args[gb_idx : gb_idx + 4]

    def test_multi_column_order_by_preserved(
        self, translator: Translator, basic_index: str
    ):
        """ORDER BY a ASC, b DESC must not drop the trailing key.

        FT.SEARCH SORTBY only accepts one key; FT.AGGREGATE SORTBY accepts
        multiple. The translator auto-routes multi-key ORDER BY to
        FT.AGGREGATE so both keys survive.
        """
        result = translator.translate(
            f"SELECT * FROM {basic_index} ORDER BY price ASC, title DESC"
        )
        # Multi-key ORDER BY triggers FT.AGGREGATE
        assert result.command == "FT.AGGREGATE"
        assert "SORTBY" in result.args
        sb_idx = result.args.index("SORTBY")
        # FT.AGGREGATE SORTBY format: SORTBY nargs @field dir @field dir
        assert result.args[sb_idx + 1] == "4"  # 2 keys * 2 (field+dir)
        assert result.args[sb_idx + 2] == "@price"
        assert result.args[sb_idx + 3] == "ASC"
        assert result.args[sb_idx + 4] == "@title"
        assert result.args[sb_idx + 5] == "DESC"

    def test_multi_column_order_by_loads_non_select_field(
        self, translator: Translator, basic_index: str
    ):
        """When ORDER BY references a column outside SELECT, LOAD must
        include it so the sort works on non-SORTABLE columns."""
        result = translator.translate(
            f"SELECT title FROM {basic_index} " "ORDER BY price ASC, content DESC"
        )
        assert result.command == "FT.AGGREGATE"
        assert "LOAD" in result.args
        load_idx = result.args.index("LOAD")
        load_count = int(result.args[load_idx + 1])
        loaded = result.args[load_idx + 2 : load_idx + 2 + load_count]
        # The ORDER BY columns must be loaded along with the SELECT column
        assert "@price" in loaded
        assert "@content" in loaded
        assert "@title" in loaded


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
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            translator.translate(f"SELECT * FROM {basic_index} WHERE status IS NULL")
            assert len(w) == 1
            assert "Redis 7.4+" in str(w[0].message)
            assert "INDEXMISSING" in str(w[0].message)

    def test_is_not_null_emits_warning(self, translator: Translator, basic_index: str):
        """IS NOT NULL translation emits a warning about Redis version requirement."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            translator.translate(
                f"SELECT * FROM {basic_index} WHERE status IS NOT NULL"
            )
            assert len(w) == 1
            assert "Redis 7.4+" in str(w[0].message)


class TestTranslatorExists:
    """Tests for exists() → FT.AGGREGATE APPLY/FILTER translation."""

    def test_exists_in_select(self, translator: Translator, basic_index: str):
        """exists(field) in SELECT generates APPLY exists(@field)."""
        result = translator.translate(
            f"SELECT exists(status) AS has_status FROM {basic_index}"
        )
        assert result.command == "FT.AGGREGATE"
        assert "APPLY" in result.args
        apply_idx = result.args.index("APPLY")
        assert "exists(@status)" in result.args[apply_idx + 1]
        assert result.args[apply_idx + 2] == "AS"
        assert result.args[apply_idx + 3] == "has_status"

    def test_exists_loads_referenced_field(
        self, translator: Translator, basic_index: str
    ):
        """Fields inside exists() must be LOADed."""
        result = translator.translate(
            f"SELECT exists(status) AS has_status FROM {basic_index}"
        )
        assert "LOAD" in result.args
        load_idx = result.args.index("LOAD")
        load_count = int(result.args[load_idx + 1])
        load_fields = result.args[load_idx + 2 : load_idx + 2 + load_count]
        assert "@status" in load_fields

    def test_multiple_exists_in_select(self, translator: Translator, basic_index: str):
        """Multiple exists() in SELECT generate multiple APPLY clauses."""
        result = translator.translate(
            f"SELECT exists(status) AS has_status, exists(category) AS has_cat "
            f"FROM {basic_index}"
        )
        assert result.command == "FT.AGGREGATE"
        # Should have two APPLY clauses
        apply_count = result.args.count("APPLY")
        assert apply_count == 2

    def test_exists_in_having_generates_filter(
        self, translator: Translator, basic_index: str
    ):
        """HAVING exists(field) generates FILTER exists(@field)."""
        result = translator.translate(
            f"SELECT title, status FROM {basic_index} HAVING exists(status)"
        )
        assert result.command == "FT.AGGREGATE"
        assert "FILTER" in result.args
        filter_idx = result.args.index("FILTER")
        assert "exists(@status)" in result.args[filter_idx + 1]

    def test_exists_in_having_loads_field(
        self, translator: Translator, basic_index: str
    ):
        """Fields inside HAVING exists() must be LOADed."""
        result = translator.translate(
            f"SELECT title FROM {basic_index} HAVING exists(status)"
        )
        assert "LOAD" in result.args
        load_idx = result.args.index("LOAD")
        load_count = int(result.args[load_idx + 1])
        load_fields = result.args[load_idx + 2 : load_idx + 2 + load_count]
        assert "@status" in load_fields

    def test_exists_includes_dialect_2(self, translator: Translator, basic_index: str):
        """exists() commands still end with DIALECT 2."""
        result = translator.translate(
            f"SELECT exists(status) AS has_status FROM {basic_index}"
        )
        assert result.args[-2:] == ["DIALECT", "2"]

    def test_exists_arithmetic_loads_fields(
        self, translator: Translator, basic_index: str
    ):
        """exists() in arithmetic expressions must LOAD referenced fields.

        sqlglot uppercases exists() to EXISTS() in arithmetic context.
        The LOAD extraction must be case-insensitive.
        """
        result = translator.translate(
            f"SELECT exists(status) + exists(category) AS total FROM {basic_index}"
        )
        assert result.command == "FT.AGGREGATE"
        assert "LOAD" in result.args
        load_idx = result.args.index("LOAD")
        load_count = int(result.args[load_idx + 1])
        load_fields = result.args[load_idx + 2 : load_idx + 2 + load_count]
        assert "@status" in load_fields
        assert "@category" in load_fields

    def test_select_star_with_having_uses_load_all(
        self, translator: Translator, basic_index: str
    ):
        """SELECT * with HAVING forces FT.AGGREGATE and must emit LOAD *."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} HAVING exists(status)"
        )
        assert result.command == "FT.AGGREGATE"
        assert "LOAD" in result.args
        load_idx = result.args.index("LOAD")
        assert result.args[load_idx + 1] == "*"


class TestTranslatorFuzzyLevels:
    """Tests for FUZZY with Levenshtein distance levels.

    Inspired by PostgreSQL's pg_trgm similarity threshold levels,
    maps to RediSearch's %, %%, %%% fuzzy syntax.
    """

    def test_fuzzy_ld1_default(self, translator: Translator, basic_index: str):
        """fuzzy(field, 'term') with no level → LD=1 (%term%)."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE fuzzy(title, 'laptap')"
        )
        assert result.command == "FT.SEARCH"
        assert "@title:%laptap%" in result.query_string

    def test_fuzzy_ld2(self, translator: Translator, basic_index: str):
        """fuzzy(field, 'term', 2) → LD=2 (%%term%%)."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE fuzzy(title, 'laptap', 2)"
        )
        assert "@title:%%laptap%%" in result.query_string

    def test_fuzzy_ld3(self, translator: Translator, basic_index: str):
        """fuzzy(field, 'term', 3) → LD=3 (%%%term%%%)."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE fuzzy(title, 'laptap', 3)"
        )
        assert "@title:%%%laptap%%%" in result.query_string

    def test_fuzzy_on_tag_field_raises(self, translator: Translator, basic_index: str):
        """fuzzy() on a TAG field raises ValueError."""
        with pytest.raises(ValueError, match="can only be used on TEXT fields"):
            translator.translate(
                f"SELECT * FROM {basic_index} WHERE fuzzy(category, 'laptap')"
            )

    def test_fulltext_on_numeric_field_raises(
        self, translator: Translator, basic_index: str
    ):
        """fulltext() on a NUMERIC field raises ValueError."""
        with pytest.raises(ValueError, match="can only be used on TEXT fields"):
            translator.translate(
                f"SELECT * FROM {basic_index} WHERE fulltext(price, 'laptop')"
            )

    def test_like_on_tag_field_raises(self, translator: Translator, basic_index: str):
        """LIKE on a TAG field raises ValueError."""
        with pytest.raises(ValueError, match="can only be used on TEXT fields"):
            translator.translate(
                f"SELECT * FROM {basic_index} WHERE category LIKE '%phone%'"
            )


class TestTranslatorSuffixInfix:
    """Tests for suffix and infix (contains) pattern matching.

    PostgreSQL analogy: LIKE '%term' and LIKE '%term%'.
    RediSearch uses *term and *term* respectively.
    """

    def test_suffix_match(self, translator: Translator, basic_index: str):
        """LIKE '%phone' → suffix match @field:*phone."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE title LIKE '%phone'"
        )
        assert "@title:*phone" in result.query_string

    def test_infix_match(self, translator: Translator, basic_index: str):
        """LIKE '%phone%' → infix/contains match @field:*phone*."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE title LIKE '%phone%'"
        )
        assert "@title:*phone*" in result.query_string

    def test_prefix_still_works(self, translator: Translator, basic_index: str):
        """LIKE 'lap%' → prefix match @field:lap* (unchanged)."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE title LIKE 'lap%'"
        )
        assert "@title:lap*" in result.query_string


class TestTranslatorORInText:
    """Tests for OR/union within text field searches.

    Inspired by PostgreSQL's to_tsquery('fat | rat') and
    websearch_to_tsquery('fat OR rat') — natural OR syntax
    maps to RediSearch's @field:(term1|term2).
    """

    def test_fulltext_or(self, translator: Translator, basic_index: str):
        """fulltext(field, 'laptop OR tablet') → @field:(laptop|tablet)."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE fulltext(title, 'laptop OR tablet')"
        )
        assert "@title:(laptop|tablet)" in result.query_string

    def test_fulltext_multiple_or(self, translator: Translator, basic_index: str):
        """fulltext(field, 'a OR b OR c') → @field:(a|b|c)."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE fulltext(title, 'laptop OR tablet OR phone')"
        )
        assert "@title:(laptop|tablet|phone)" in result.query_string


class TestTranslatorProximity:
    """Tests for proximity search (slop + inorder).

    Inspired by PostgreSQL's phraseto_tsquery / <N> FOLLOWED BY operator.
    Maps to RediSearch query attributes: => { $slop: N; $inorder: true; }.
    """

    def test_fulltext_with_slop(self, translator: Translator, basic_index: str):
        """fulltext(field, 'gaming laptop', 2) → slop=2 query attribute."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE fulltext(title, 'gaming laptop', 2)"
        )
        assert "$slop: 2;" in result.query_string

    def test_fulltext_with_slop_and_inorder(
        self, translator: Translator, basic_index: str
    ):
        """fulltext(field, 'gaming laptop', 2, true) → slop=2 + inorder."""
        result = translator.translate(
            f"SELECT * FROM {basic_index} WHERE fulltext(title, 'gaming laptop', 2, true)"
        )
        assert "$slop: 2;" in result.query_string
        assert "$inorder: true;" in result.query_string


class TestTranslatorScoring:
    """Tests for relevance scoring (WITHSCORES + SCORER).

    Inspired by PostgreSQL's ts_rank(vector, query) AS rank in SELECT.
    Maps to RediSearch's WITHSCORES and SCORER flags on FT.SEARCH.

    SQL: SELECT name, score() AS relevance FROM idx WHERE fulltext(...)
    Redis: FT.SEARCH idx "@field:(term)" WITHSCORES SCORER BM25
    """

    def test_score_default_bm25(self, translator: Translator, basic_index: str):
        """score() in SELECT → WITHSCORES + SCORER BM25."""
        result = translator.translate(
            f"SELECT title, score() AS relevance FROM {basic_index} WHERE fulltext(title, 'laptop')"
        )
        assert "WITHSCORES" in result.args
        assert "SCORER" in result.args
        scorer_idx = result.args.index("SCORER")
        assert result.args[scorer_idx + 1] == "BM25"

    def test_score_custom_scorer(self, translator: Translator, basic_index: str):
        """score('TFIDF') in SELECT → WITHSCORES + SCORER TFIDF."""
        result = translator.translate(
            f"SELECT title, score('TFIDF') AS relevance FROM {basic_index} WHERE fulltext(title, 'laptop')"
        )
        assert "WITHSCORES" in result.args
        scorer_idx = result.args.index("SCORER")
        assert result.args[scorer_idx + 1] == "TFIDF"

    def test_score_custom_scorer_preserves_case(
        self, translator: Translator, basic_index: str
    ):
        """score('MyScorer') preserves caller-provided casing."""
        result = translator.translate(
            f"SELECT title, score('MyScorer') AS relevance FROM {basic_index} "
            "WHERE fulltext(title, 'laptop')"
        )
        scorer_idx = result.args.index("SCORER")
        assert result.args[scorer_idx + 1] == "MyScorer"

    def test_duplicate_score_raises(self, translator: Translator, basic_index: str):
        """Multiple score() expressions in the same query raise ValueError."""
        with pytest.raises(ValueError, match="Only one score"):
            translator.translate(
                f"SELECT score() AS s1, score('TFIDF') AS s2 FROM {basic_index} "
                "WHERE fulltext(title, 'laptop')"
            )

    def test_no_score_no_withscores(self, translator: Translator, basic_index: str):
        """Without score() → no WITHSCORES flag."""
        result = translator.translate(
            f"SELECT title FROM {basic_index} WHERE fulltext(title, 'laptop')"
        )
        assert "WITHSCORES" not in result.args

    def test_score_only_select_emits_return_0(
        self, translator: Translator, basic_index: str
    ):
        """SELECT score() AS relevance (no other fields) → RETURN 0 to prevent payload leak."""
        result = translator.translate(
            f"SELECT score() AS relevance FROM {basic_index} WHERE fulltext(title, 'laptop')"
        )
        assert "RETURN" in result.args
        ret_idx = result.args.index("RETURN")
        assert result.args[ret_idx + 1] == "0"
        assert "WITHSCORES" in result.args

    def test_score_with_aggregate_raises(
        self, translator: Translator, basic_index: str
    ):
        """score() combined with GROUP BY (forces FT.AGGREGATE) raises ValueError."""
        with pytest.raises(ValueError, match="score.*not supported.*FT.AGGREGATE"):
            translator.translate(
                f"SELECT COUNT(*), score() AS relevance FROM {basic_index} "
                "WHERE fulltext(title, 'laptop') GROUP BY category"
            )

    def test_score_too_many_args_raises(self, translator: Translator, basic_index: str):
        """score() with more than one argument raises ValueError."""
        with pytest.raises(ValueError, match="at most one argument"):
            translator.translate(
                f"SELECT score('BM25', 'extra') AS relevance FROM {basic_index} "
                "WHERE fulltext(title, 'laptop')"
            )

    def test_order_by_score_desc_omits_sortby(
        self, translator: Translator, basic_index: str
    ):
        """ORDER BY score_alias DESC omits SORTBY (RediSearch sorts by relevance by default)."""
        result = translator.translate(
            f"SELECT title, score() AS relevance FROM {basic_index} "
            "WHERE fulltext(title, 'laptop') ORDER BY relevance DESC"
        )
        assert "WITHSCORES" in result.args
        assert "SORTBY" not in result.args

    def test_order_by_score_asc_raises(self, translator: Translator, basic_index: str):
        """ORDER BY score_alias ASC raises ValueError (not supported by RediSearch)."""
        with pytest.raises(ValueError, match="ASC is not supported"):
            translator.translate(
                f"SELECT title, score() AS relevance FROM {basic_index} "
                "WHERE fulltext(title, 'laptop') ORDER BY relevance ASC"
            )

    def test_order_by_real_field_with_score_still_works(
        self, translator: Translator, basic_index: str
    ):
        """ORDER BY a real field (not score alias) still emits SORTBY."""
        result = translator.translate(
            f"SELECT title, score() AS relevance FROM {basic_index} "
            "WHERE fulltext(title, 'laptop') ORDER BY price DESC"
        )
        assert "SORTBY" in result.args
        idx = result.args.index("SORTBY")
        assert result.args[idx + 1] == "price"
