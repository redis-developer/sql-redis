"""Tests for the SQL parser component."""

import pytest

from sql_redis.parser import SQLParser


class TestSQLParserSelectClause:
    """Tests for parsing SELECT clause."""

    def test_parse_simple_field_list(self):
        """Parse SELECT with simple field list."""
        parser = SQLParser()
        result = parser.parse("SELECT title, price FROM products")

        assert result.fields == ["title", "price"]
        assert result.index == "products"

    def test_parse_select_star(self):
        """Parse SELECT *."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM products")

        assert result.fields == ["*"]

    def test_parse_aggregation_functions(self):
        """Parse SELECT with aggregation functions."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT category, COUNT(*) AS count, SUM(price) AS total FROM products"
        )

        assert "category" in result.fields
        assert len(result.aggregations) == 2
        assert result.aggregations[0].function == "COUNT"
        assert result.aggregations[0].alias == "count"
        assert result.aggregations[1].function == "SUM"
        assert result.aggregations[1].field == "price"
        assert result.aggregations[1].alias == "total"

    def test_parse_computed_field(self):
        """Parse SELECT with computed expression."""
        parser = SQLParser()
        result = parser.parse("SELECT price, (price * 0.9) AS discounted FROM products")

        assert len(result.computed_fields) == 1
        assert result.computed_fields[0].expression == "price * 0.9"
        assert result.computed_fields[0].alias == "discounted"

    def test_parse_computed_field_without_parens(self):
        """Parse SELECT with arithmetic expression without parentheses."""
        parser = SQLParser()
        result = parser.parse("SELECT price * 0.9 AS discounted FROM products")

        assert len(result.computed_fields) == 1
        assert result.computed_fields[0].expression == "price * 0.9"
        assert result.computed_fields[0].alias == "discounted"

    def test_parse_computed_field_without_alias(self):
        """Parse SELECT with computed expression without alias."""
        parser = SQLParser()
        result = parser.parse("SELECT (price * 0.9) FROM products")

        assert len(result.computed_fields) == 1
        assert result.computed_fields[0].expression == "price * 0.9"
        # Alias defaults to the expression itself
        assert result.computed_fields[0].alias == "price * 0.9"

    def test_parse_vector_distance_function(self):
        """Parse SELECT with vector_distance function."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT id, vector_distance(embedding, :vector) AS similarity FROM vectors"
        )

        assert result.vector_search is not None
        assert result.vector_search.field == "embedding"
        assert result.vector_search.alias == "similarity"


class TestSQLParserFromClause:
    """Tests for parsing FROM clause."""

    def test_parse_simple_from(self):
        """Parse simple FROM clause."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM products")

        assert result.index == "products"

    def test_parse_from_with_alias(self):
        """Parse FROM with table alias."""
        parser = SQLParser()
        result = parser.parse("SELECT p.title FROM products p")

        assert result.index == "products"


class TestSQLParserWhereClause:
    """Tests for parsing WHERE clause."""

    def test_parse_equality_condition(self):
        """Parse WHERE with equality."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM products WHERE category = 'electronics'")

        assert len(result.conditions) == 1
        assert result.conditions[0].field == "category"
        assert result.conditions[0].operator == "="
        assert result.conditions[0].value == "electronics"

    def test_parse_numeric_comparison(self):
        """Parse WHERE with numeric comparison."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM products WHERE price > 100")

        assert result.conditions[0].field == "price"
        assert result.conditions[0].operator == ">"
        assert result.conditions[0].value == 100

    def test_parse_between(self):
        """Parse WHERE with BETWEEN."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM products WHERE price BETWEEN 100 AND 500")

        assert result.conditions[0].field == "price"
        assert result.conditions[0].operator == "BETWEEN"
        assert result.conditions[0].value == (100, 500)

    def test_parse_in_clause(self):
        """Parse WHERE with IN."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT * FROM products WHERE tags IN ('sale', 'featured')"
        )

        assert result.conditions[0].field == "tags"
        assert result.conditions[0].operator == "IN"
        assert result.conditions[0].value == ["sale", "featured"]

    def test_parse_fulltext_function(self):
        """Parse WHERE with fulltext function."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM products WHERE fulltext(title, 'laptop')")

        assert result.conditions[0].field == "title"
        assert result.conditions[0].operator == "FULLTEXT"
        assert result.conditions[0].value == "laptop"

    def test_parse_and_conditions(self):
        """Parse WHERE with AND."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT * FROM products WHERE price > 100 AND category = 'electronics'"
        )

        assert len(result.conditions) == 2
        assert result.boolean_operator == "AND"

    def test_parse_or_conditions(self):
        """Parse WHERE with OR."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT * FROM products WHERE category = 'books' OR category = 'electronics'"
        )

        assert len(result.conditions) == 2
        assert result.boolean_operator == "OR"

    def test_parse_not_condition(self):
        """Parse WHERE with NOT."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT * FROM products WHERE NOT category = 'electronics'"
        )

        assert result.conditions[0].negated is True

    def test_parse_geo_distance_comparison(self):
        """Parse WHERE with geo_distance function comparison.

        Note: POINT(lon, lat) matches Redis's native format.
        """
        parser = SQLParser()
        # POINT(lon, lat) - matches Redis native format
        result = parser.parse(
            "SELECT name FROM stores WHERE geo_distance(location, POINT(-122.4, 37.8)) < 10"
        )

        assert len(result.geo_conditions) == 1
        assert result.geo_conditions[0].field == "location"
        assert result.geo_conditions[0].operator == "<"
        assert result.geo_conditions[0].radius == 10.0
        # POINT(lon, lat) - matches Redis native format
        assert result.geo_conditions[0].lon == -122.4
        assert result.geo_conditions[0].lat == 37.8

    def test_parse_less_than_or_equal(self):
        """Parse WHERE with <= comparison."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM products WHERE price <= 100")

        assert result.conditions[0].operator == "<="

    def test_parse_greater_than_or_equal(self):
        """Parse WHERE with >= comparison."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM products WHERE rating >= 4")

        assert result.conditions[0].operator == ">="

    def test_parse_not_equal(self):
        """Parse WHERE with != comparison."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM products WHERE status != 'deleted'")

        assert result.conditions[0].operator == "!="
        assert result.conditions[0].value == "deleted"


class TestSQLParserGroupByClause:
    """Tests for parsing GROUP BY clause."""

    def test_parse_simple_group_by(self):
        """Parse GROUP BY with single column."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT category, COUNT(*) FROM products GROUP BY category"
        )

        assert result.groupby_fields == ["category"]

    def test_parse_multiple_group_by(self):
        """Parse GROUP BY with multiple columns."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT category, status, COUNT(*) FROM products GROUP BY category, status"
        )

        assert result.groupby_fields == ["category", "status"]


class TestSQLParserOrderByClause:
    """Tests for parsing ORDER BY clause."""

    def test_parse_order_by_asc(self):
        """Parse ORDER BY with ASC (default)."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM products ORDER BY price")

        assert result.orderby_fields == [("price", "ASC")]

    def test_parse_order_by_desc(self):
        """Parse ORDER BY with DESC."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM products ORDER BY price DESC")

        assert result.orderby_fields == [("price", "DESC")]

    def test_parse_order_by_multiple(self):
        """Parse ORDER BY with multiple columns."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT * FROM products ORDER BY category ASC, price DESC"
        )

        assert result.orderby_fields == [("category", "ASC"), ("price", "DESC")]


class TestSQLParserLimitOffset:
    """Tests for parsing LIMIT and OFFSET clauses."""

    def test_parse_limit(self):
        """Parse LIMIT clause."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM products LIMIT 10")

        assert result.limit == 10

    def test_parse_limit_offset(self):
        """Parse LIMIT with OFFSET."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM products LIMIT 10 OFFSET 20")

        assert result.limit == 10
        assert result.offset == 20

    def test_parse_limit_with_order_by(self):
        """Parse LIMIT with ORDER BY."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM products ORDER BY price DESC LIMIT 5")

        assert result.orderby_fields == [("price", "DESC")]
        assert result.limit == 5


class TestSQLParserBuiltInFunctions:
    """Tests for parsing built-in SQL functions."""

    def test_parse_upper_function(self):
        """Parse UPPER function as computed field."""
        parser = SQLParser()
        result = parser.parse("SELECT UPPER(name) AS upper_name FROM products")

        assert len(result.computed_fields) == 1
        assert "UPPER" in result.computed_fields[0].expression.upper()
        assert result.computed_fields[0].alias == "upper_name"

    def test_parse_lower_function(self):
        """Parse LOWER function as computed field."""
        parser = SQLParser()
        result = parser.parse("SELECT LOWER(name) AS lower_name FROM products")

        assert len(result.computed_fields) == 1
        assert "LOWER" in result.computed_fields[0].expression.upper()
        assert result.computed_fields[0].alias == "lower_name"

    def test_parse_custom_function_as_computed(self):
        """Parse custom (non-vector) function as computed field."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT my_custom_func(field1, field2) AS result FROM products"
        )

        assert len(result.computed_fields) == 1
        assert "my_custom_func" in result.computed_fields[0].expression.lower()


class TestSQLParserEdgeCases:
    """Tests for edge cases and branch coverage."""

    def test_parse_min_max_aggregations(self):
        """Parse MIN and MAX aggregation functions."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT MIN(price) AS min_price, MAX(price) AS max_price FROM products"
        )

        assert len(result.aggregations) == 2
        assert result.aggregations[0].function == "MIN"
        assert result.aggregations[1].function == "MAX"

    def test_parse_aggregation_on_field(self):
        """Parse aggregation on specific field (not *)."""
        parser = SQLParser()
        result = parser.parse("SELECT AVG(rating) AS avg_rating FROM products")

        assert len(result.aggregations) == 1
        assert result.aggregations[0].function == "AVG"
        assert result.aggregations[0].field == "rating"

    def test_parse_division_computed_field(self):
        """Parse division expression as computed field."""
        parser = SQLParser()
        result = parser.parse("SELECT price / 100 AS price_cents FROM products")

        assert len(result.computed_fields) == 1
        assert "/" in result.computed_fields[0].expression

    def test_parse_addition_computed_field(self):
        """Parse addition expression as computed field."""
        parser = SQLParser()
        result = parser.parse("SELECT price + tax AS total FROM products")

        assert len(result.computed_fields) == 1
        assert "+" in result.computed_fields[0].expression

    def test_parse_subtraction_computed_field(self):
        """Parse subtraction expression as computed field."""
        parser = SQLParser()
        result = parser.parse("SELECT price - discount AS final FROM products")

        assert len(result.computed_fields) == 1
        assert "-" in result.computed_fields[0].expression

    def test_parse_vector_distance_without_alias(self):
        """Parse vector_distance without explicit alias."""
        parser = SQLParser()
        result = parser.parse("SELECT vector_distance(embedding, :vec) FROM vectors")

        assert result.vector_search is not None
        assert result.vector_search.field == "embedding"
        assert result.vector_search.alias == "vector_distance"  # defaults to func name

    def test_parse_float_comparison(self):
        """Parse comparison with float value."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM products WHERE price > 99.99")

        assert result.conditions[0].value == 99.99

    def test_parse_string_in_clause(self):
        """Parse IN clause with string values."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT * FROM products WHERE status IN ('active', 'pending')"
        )

        assert result.conditions[0].operator == "IN"
        assert result.conditions[0].value == ["active", "pending"]

    def test_parse_geo_distance_greater_than(self):
        """Parse geo_distance with > operator."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT * FROM stores WHERE geo_distance(location, POINT(0, 0)) > 100"
        )

        assert result.geo_conditions[0].operator == ">"
        assert result.geo_conditions[0].radius == 100.0

    def test_parse_between_with_floats(self):
        """Parse BETWEEN with float values."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT * FROM products WHERE price BETWEEN 10.5 AND 99.9"
        )

        assert result.conditions[0].value == (10.5, 99.9)

    def test_parse_not_between(self):
        """Parse NOT BETWEEN condition."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM products WHERE NOT price BETWEEN 0 AND 10")

        assert result.conditions[0].operator == "BETWEEN"
        assert result.conditions[0].negated is True

    def test_parse_not_in(self):
        """Parse NOT IN condition."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT * FROM products WHERE NOT category IN ('hidden', 'deleted')"
        )

        assert result.conditions[0].operator == "IN"
        assert result.conditions[0].negated is True

    def test_parse_explicit_asc_order(self):
        """Parse ORDER BY with explicit ASC."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM products ORDER BY name ASC")

        assert result.orderby_fields == [("name", "ASC")]

    def test_parse_select_without_from(self):
        """Parse SELECT without FROM clause."""
        parser = SQLParser()
        result = parser.parse("SELECT 1")

        assert result.index == ""  # No index
        assert result.fields == []  # 1 is a literal, not a field

    def test_parse_count_star(self):
        """Parse COUNT(*) explicitly."""
        parser = SQLParser()
        result = parser.parse("SELECT COUNT(*) AS total FROM products")

        assert len(result.aggregations) == 1
        assert result.aggregations[0].function == "COUNT"
        assert result.aggregations[0].field is None  # * means no specific field

    def test_parse_order_by_non_column(self):
        """Parse ORDER BY with expression (handled gracefully)."""
        parser = SQLParser()
        # Should not crash, even if we don't extract the order field
        result = parser.parse("SELECT * FROM products ORDER BY 1")

        # Expression "1" isn't a column, so we may not capture it
        assert result.orderby_fields == []

    def test_parse_geo_distance_lte(self):
        """Parse geo_distance with <= operator."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT * FROM stores WHERE geo_distance(location, POINT(0, 0)) <= 50"
        )

        assert result.geo_conditions[0].operator == "<="
        assert result.geo_conditions[0].radius == 50.0

    def test_parse_geo_distance_gte(self):
        """Parse geo_distance with >= operator."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT * FROM stores WHERE geo_distance(location, POINT(0, 0)) >= 10"
        )

        assert result.geo_conditions[0].operator == ">="
        assert result.geo_conditions[0].radius == 10.0

    def test_parse_subquery_from(self):
        """Parse FROM with subquery (finds inner table)."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM (SELECT id FROM products)")

        # sqlglot.find traverses deeply, so it finds the inner table
        assert result.index == "products"

    def test_parse_group_by_ordinal(self):
        """Parse GROUP BY with ordinal position."""
        parser = SQLParser()
        result = parser.parse("SELECT category, COUNT(*) FROM products GROUP BY 1")

        # Ordinal "1" isn't a column, so we don't capture it
        assert result.groupby_fields == []

    def test_parse_count_no_argument(self):
        """Parse COUNT() without argument (edge case)."""
        parser = SQLParser()
        result = parser.parse("SELECT COUNT() FROM products")

        assert len(result.aggregations) == 1
        assert result.aggregations[0].function == "COUNT"
        assert result.aggregations[0].field is None

    def test_parse_fulltext_with_placeholder(self):
        """Parse fulltext with placeholder value."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM products WHERE fulltext(title, :query)")

        # Placeholder isn't a literal, so value may be None
        assert result.conditions[0].field == "title"
        assert result.conditions[0].operator == "FULLTEXT"

    def test_parse_limit_placeholder(self):
        """Parse LIMIT with placeholder (non-literal)."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM products LIMIT :n")

        # Placeholder isn't a literal, so limit stays None
        assert result.limit is None

    def test_parse_offset_placeholder(self):
        """Parse OFFSET with placeholder (non-literal)."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM products LIMIT 10 OFFSET :start")

        assert result.limit == 10
        assert result.offset is None  # Placeholder isn't a literal

    def test_parse_order_by_alias(self):
        """Parse ORDER BY with column alias."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT price * 0.9 AS discounted FROM products ORDER BY discounted DESC"
        )

        # 'discounted' is parsed as a Column
        assert result.orderby_fields == [("discounted", "DESC")]

    def test_parse_comparison_with_placeholder(self):
        """Parse comparison with placeholder value."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM products WHERE price > :min_price")

        # Placeholder on RHS - value will be None since not a literal
        assert result.conditions[0].field == "price"
        assert result.conditions[0].operator == ">"
        assert result.conditions[0].value is None

    def test_parse_in_with_numbers(self):
        """Parse IN clause with numeric values."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM products WHERE id IN (1, 2, 3)")

        assert result.conditions[0].operator == "IN"
        assert result.conditions[0].value == [1, 2, 3]

    def test_parse_between_field_not_column(self):
        """Parse BETWEEN where field is an expression."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT * FROM products WHERE (price * 0.9) BETWEEN 10 AND 100"
        )

        # The field is a Paren expression, not a Column - condition skipped
        assert len(result.conditions) == 0

    def test_parse_in_field_not_column(self):
        """Parse IN where field is an expression."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT * FROM products WHERE UPPER(status) IN ('ACTIVE', 'PENDING')"
        )

        # The field is a function, not a Column - condition skipped
        assert len(result.conditions) == 0

    def test_parse_vector_distance_no_args(self):
        """Parse vector_distance with no arguments (edge case)."""
        parser = SQLParser()
        result = parser.parse("SELECT vector_distance() FROM vectors")

        # No expressions, so vector_search stays None
        assert result.vector_search is None

    def test_parse_vector_distance_literal_first_arg(self):
        """Parse vector_distance with literal first arg (edge case)."""
        parser = SQLParser()
        result = parser.parse("SELECT vector_distance('field', :vec) FROM vectors")

        # First arg is string literal, not Column - so vector_search stays None
        assert result.vector_search is None

    def test_parse_non_fulltext_function_in_where(self):
        """Parse non-fulltext function in WHERE (not handled as condition)."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM products WHERE custom_func(x, y)")

        # custom_func != FULLTEXT, so condition not added
        assert len(result.conditions) == 0

    def test_parse_fulltext_non_column_first_arg(self):
        """Parse fulltext with non-column first argument raises ValueError."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="must be a column name"):
            parser.parse("SELECT * FROM products WHERE fulltext(UPPER(title), 'query')")

    def test_parse_fulltext_insufficient_args(self):
        """Parse fulltext with insufficient arguments raises ValueError."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="requires at least 2 arguments"):
            parser.parse("SELECT * FROM products WHERE fulltext(title)")

    def test_parse_geo_distance_no_args(self):
        """Parse geo_distance with no arguments in comparison."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM stores WHERE geo_distance() < 10")

        # No expressions in function - condition skipped
        assert len(result.conditions) == 0

    def test_parse_geo_distance_literal_first_arg(self):
        """Parse geo_distance with literal first argument."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT * FROM stores WHERE geo_distance('location', POINT(0, 0)) < 10"
        )

        # First arg is string, not Column - condition skipped
        assert len(result.conditions) == 0

    def test_parse_exists_subquery(self):
        """Parse EXISTS subquery (not a condition we handle)."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM products WHERE EXISTS (SELECT 1 FROM x)")

        # EXISTS isn't a condition type we extract
        assert len(result.conditions) == 0

    def test_parse_literal_comparison(self):
        """Parse comparison of literals (no column)."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM products WHERE 1 = 1")

        # LHS is literal not column - condition skipped
        assert len(result.conditions) == 0

    def test_parse_from_values_clause(self):
        """Parse FROM with VALUES clause (no table)."""
        parser = SQLParser()
        result = parser.parse("SELECT 1 FROM (VALUES (1))")

        # FROM exists but has no Table - index stays empty
        assert result.index == ""

    def test_parse_insert_statement(self):
        """Parse INSERT statement (no SELECT clause)."""
        parser = SQLParser()
        result = parser.parse("INSERT INTO products VALUES (1, 'test')")

        # No SELECT clause - fields and index stay empty
        assert result.fields == []
        assert result.index == ""

    def test_parse_count_distinct(self):
        """COUNT(DISTINCT field) routes to the COUNT_DISTINCT reducer."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT COUNT(DISTINCT category) AS unique_cats FROM products"
        )

        assert len(result.aggregations) == 1
        assert result.aggregations[0].function == "COUNT_DISTINCT"
        assert result.aggregations[0].field == "category"
        assert result.aggregations[0].alias == "unique_cats"

    def test_parse_count_distinct_without_alias(self):
        """COUNT(DISTINCT field) preserves the field even without an alias."""
        parser = SQLParser()
        result = parser.parse("SELECT COUNT(DISTINCT title) FROM products")

        assert len(result.aggregations) == 1
        assert result.aggregations[0].function == "COUNT_DISTINCT"
        assert result.aggregations[0].field == "title"

    def test_parse_sum_distinct_raises(self):
        """SUM(DISTINCT field) is rejected — RediSearch has no equivalent."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="DISTINCT"):
            parser.parse("SELECT SUM(DISTINCT price) FROM products")

    def test_parse_avg_distinct_raises(self):
        """AVG(DISTINCT field) is rejected — RediSearch has no equivalent."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="DISTINCT"):
            parser.parse("SELECT AVG(DISTINCT price) FROM products")

    def test_parse_count_distinct_multi_column_raises(self):
        """COUNT(DISTINCT a, b) is rejected — RediSearch has no equivalent."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="single column"):
            parser.parse("SELECT COUNT(DISTINCT a, b) FROM products")

    def test_parse_sum_expression(self):
        """Parse SUM of expression - not a simple Column."""
        parser = SQLParser()
        result = parser.parse("SELECT SUM(price * quantity) AS total FROM products")

        # Multiplication isn't a Column
        assert len(result.aggregations) == 1
        assert result.aggregations[0].function == "SUM"
        assert result.aggregations[0].field is None


class TestSQLParserComplexQueries:
    """Tests for complex multi-clause queries."""

    def test_parse_full_query(self):
        """Parse query with all clauses."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT category, COUNT(*) AS cnt, AVG(price) AS avg_price "
            "FROM products "
            "WHERE price > 10 AND category != 'hidden' "
            "GROUP BY category "
            "ORDER BY avg_price DESC "
            "LIMIT 10 OFFSET 0"
        )

        assert result.index == "products"
        assert "category" in result.fields
        assert len(result.aggregations) == 2
        assert len(result.conditions) == 2
        assert result.groupby_fields == ["category"]
        assert result.orderby_fields == [("avg_price", "DESC")]
        assert result.limit == 10
        assert result.offset == 0

    def test_parse_vector_search_with_filter(self):
        """Parse vector search query with prefilter."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT id, vector_distance(embedding, :query_vec) AS score "
            "FROM vectors "
            "WHERE category = 'tech' "
            "ORDER BY score ASC "
            "LIMIT 5"
        )

        assert result.vector_search is not None
        assert result.vector_search.field == "embedding"
        assert len(result.conditions) == 1
        assert result.conditions[0].field == "category"
        assert result.limit == 5


class TestSQLParserParenthesizedConditions:
    """Tests for parenthesized WHERE conditions (exp.Paren handling)."""

    def test_parenthesized_single_condition(self):
        """Parenthesized conditions are not silently dropped."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM idx WHERE (status = 'active')")
        assert len(result.conditions) == 1
        assert result.conditions[0].field == "status"
        assert result.conditions[0].value == "active"

    def test_not_parenthesized_condition(self):
        """NOT (...) with exp.Paren unwraps correctly."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM idx WHERE NOT (status = 'active')")
        assert len(result.conditions) == 1
        assert result.conditions[0].negated is True
        assert result.conditions[0].field == "status"


class TestSQLParserMixedBooleanLogic:
    """Tests that the WHERE-clause boolean tree preserves AND/OR grouping.

    Regression coverage for bugs where ``A AND (B OR C)``,
    ``A OR (B AND C)`` and similar mixed expressions collapsed onto a single
    ``boolean_operator`` and a flat ``conditions`` list, losing the grouping
    expressed by the SQL parentheses.
    """

    def _fields(self, node):
        """Recursively extract leaf field names from a BoolNode tree."""
        from sql_redis.parser import BoolGroup, BoolLeaf

        if isinstance(node, BoolLeaf):
            return [node.condition.field]
        if isinstance(node, BoolGroup):
            return [f for c in node.children for f in self._fields(c)]
        return []

    def test_and_with_nested_or_keeps_group(self):
        """A AND (B OR C): root is AND, with an inner OR child."""
        from sql_redis.parser import BoolGroup, BoolLeaf

        parser = SQLParser()
        result = parser.parse(
            "SELECT * FROM idx WHERE a = '1' AND (b = '2' OR c = '3')"
        )

        tree = result.condition_tree
        assert isinstance(tree, BoolGroup)
        assert tree.operator == "AND"
        assert len(tree.children) == 2
        # First child is the leaf `a = '1'`
        assert isinstance(tree.children[0], BoolLeaf)
        assert tree.children[0].condition.field == "a"
        # Second child is the OR group with b and c
        inner = tree.children[1]
        assert isinstance(inner, BoolGroup)
        assert inner.operator == "OR"
        assert self._fields(inner) == ["b", "c"]

    def test_or_with_nested_and_keeps_group(self):
        """A OR (B AND C): root is OR, with an inner AND child."""
        from sql_redis.parser import BoolGroup, BoolLeaf

        parser = SQLParser()
        result = parser.parse(
            "SELECT * FROM idx WHERE a = '1' OR (b = '2' AND c = '3')"
        )

        tree = result.condition_tree
        assert isinstance(tree, BoolGroup)
        assert tree.operator == "OR"
        assert len(tree.children) == 2
        assert isinstance(tree.children[0], BoolLeaf)
        assert tree.children[0].condition.field == "a"
        inner = tree.children[1]
        assert isinstance(inner, BoolGroup)
        assert inner.operator == "AND"
        assert self._fields(inner) == ["b", "c"]

    def test_or_group_first_then_and(self):
        """(B OR C) AND A keeps the OR group as the first child."""
        from sql_redis.parser import BoolGroup, BoolLeaf

        parser = SQLParser()
        result = parser.parse(
            "SELECT * FROM idx WHERE (b = '2' OR c = '3') AND a = '1'"
        )

        tree = result.condition_tree
        assert isinstance(tree, BoolGroup)
        assert tree.operator == "AND"
        assert isinstance(tree.children[0], BoolGroup)
        assert tree.children[0].operator == "OR"
        assert self._fields(tree.children[0]) == ["b", "c"]
        assert isinstance(tree.children[1], BoolLeaf)
        assert tree.children[1].condition.field == "a"

    def test_chained_ands_with_trailing_or_group(self):
        """A AND B AND C AND (D OR E) flattens AND children and keeps OR."""
        from sql_redis.parser import BoolGroup

        parser = SQLParser()
        result = parser.parse(
            "SELECT * FROM idx "
            "WHERE a = '1' AND b = '2' AND c = '3' AND (d = '4' OR e = '5')"
        )

        tree = result.condition_tree
        assert isinstance(tree, BoolGroup)
        assert tree.operator == "AND"
        # Three AND leaves followed by an OR group — same-operator subtrees
        # are flattened so the AND group has 4 children, not nested.
        assert len(tree.children) == 4
        assert self._fields(tree.children[0]) == ["a"]
        assert self._fields(tree.children[1]) == ["b"]
        assert self._fields(tree.children[2]) == ["c"]
        last = tree.children[3]
        assert isinstance(last, BoolGroup)
        assert last.operator == "OR"
        assert self._fields(last) == ["d", "e"]

    def test_flat_and_chain_is_single_group(self):
        """A AND B AND C produces one AND group with three children."""
        from sql_redis.parser import BoolGroup

        parser = SQLParser()
        result = parser.parse("SELECT * FROM idx WHERE a = '1' AND b = '2' AND c = '3'")

        tree = result.condition_tree
        assert isinstance(tree, BoolGroup)
        assert tree.operator == "AND"
        assert len(tree.children) == 3
        assert self._fields(tree) == ["a", "b", "c"]


class TestSQLParserIsNull:
    """Tests for IS NULL / IS NOT NULL parsing."""

    def test_is_null_parsed(self):
        """IS NULL produces IS_NULL condition."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM idx WHERE email IS NULL")
        assert len(result.conditions) == 1
        assert result.conditions[0].operator == "IS_NULL"
        assert result.conditions[0].field == "email"
        assert result.conditions[0].value is None
        assert result.conditions[0].negated is False

    def test_is_not_null_parsed(self):
        """IS NOT NULL produces IS_NOT_NULL condition."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM idx WHERE email IS NOT NULL")
        assert len(result.conditions) == 1
        assert result.conditions[0].operator == "IS_NOT_NULL"
        assert result.conditions[0].negated is False

    def test_is_null_with_other_conditions(self):
        """IS NULL alongside regular conditions."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT * FROM idx WHERE status = 'active' AND email IS NULL"
        )
        assert len(result.conditions) == 2
        operators = {c.operator for c in result.conditions}
        assert "IS_NULL" in operators
        assert "=" in operators

    def test_double_negation_not_is_not_null(self):
        """NOT (email IS NOT NULL) should produce IS_NULL (double negation cancels)."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM idx WHERE NOT (email IS NOT NULL)")
        assert len(result.conditions) == 1
        assert result.conditions[0].operator == "IS_NULL"
        assert result.conditions[0].field == "email"

    def test_double_negation_not_is_null(self):
        """NOT (email IS NULL) should produce IS_NOT_NULL."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM idx WHERE NOT (email IS NULL)")
        assert len(result.conditions) == 1
        assert result.conditions[0].operator == "IS_NOT_NULL"
        assert result.conditions[0].field == "email"


class TestSQLParserExists:
    """Tests for exists() parsing."""

    def test_exists_in_select_produces_computed_field(self):
        """exists(field) in SELECT produces a computed field."""
        parser = SQLParser()
        result = parser.parse("SELECT exists(email) AS has_email FROM idx")
        assert len(result.computed_fields) == 1
        assert "exists" in result.computed_fields[0].expression.lower()
        assert "email" in result.computed_fields[0].expression
        assert result.computed_fields[0].alias == "has_email"

    def test_exists_without_alias_auto_generates(self):
        """exists(field) without alias generates one."""
        parser = SQLParser()
        result = parser.parse("SELECT exists(email) FROM idx")
        assert len(result.computed_fields) == 1
        assert result.computed_fields[0].alias is not None
        assert result.computed_fields[0].alias != ""

    def test_multiple_exists_in_select(self):
        """Multiple exists() in SELECT produce multiple computed fields."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT exists(email) AS has_email, exists(phone) AS has_phone FROM idx"
        )
        assert len(result.computed_fields) == 2

    def test_exists_in_having_produces_filter(self):
        """exists() in HAVING produces a filter expression."""
        parser = SQLParser()
        result = parser.parse("SELECT name FROM idx HAVING exists(email)")
        assert len(result.filters) == 1
        assert "exists" in result.filters[0].lower()
        assert "email" in result.filters[0]

    def test_exists_in_where_raises_error(self):
        """exists() in WHERE raises ValueError (only valid in aggregate context)."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="exists.*aggregate"):
            parser.parse("SELECT * FROM idx WHERE exists(email)")


class TestSQLParserFulltextValidation:
    """Tests for fulltext() argument validation."""

    def test_fulltext_negative_slop_raises(self):
        """Negative slop in fulltext() raises ValueError."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="non-negative integer"):
            parser.parse("SELECT * FROM idx WHERE fulltext(title, 'hello world', -1)")

    def test_fulltext_float_slop_raises(self):
        """Float slop in fulltext() raises ValueError instead of silently truncating."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="must be an integer"):
            parser.parse("SELECT * FROM idx WHERE fulltext(title, 'hello world', 2.9)")

    def test_fuzzy_float_level_raises(self):
        """Float fuzzy level raises ValueError instead of silently truncating."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="must be an integer"):
            parser.parse("SELECT * FROM idx WHERE fuzzy(title, 'laptap', 2.9)")

    def test_fulltext_boolean_slop_raises(self):
        """Boolean slop in fulltext() raises ValueError."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="must be an integer"):
            parser.parse("SELECT * FROM idx WHERE fulltext(title, 'hello world', true)")

    def test_fuzzy_boolean_level_raises(self):
        """Boolean fuzzy level raises ValueError."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="must be an integer"):
            parser.parse("SELECT * FROM idx WHERE fuzzy(title, 'laptap', true)")

    def test_fulltext_invalid_inorder_raises(self):
        """Invalid inorder value (e.g., 'yes') raises ValueError."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="inorder argument must be a boolean"):
            parser.parse(
                "SELECT * FROM idx WHERE fulltext(title, 'hello world', 0, 'yes')"
            )

    def test_fulltext_no_value_raises(self):
        """fulltext() with only field arg raises ValueError."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="requires at least 2 arguments"):
            parser.parse("SELECT * FROM idx WHERE fulltext(title)")

    def test_fuzzy_no_value_raises(self):
        """fuzzy() with only field arg raises ValueError."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="requires at least 2 arguments"):
            parser.parse("SELECT * FROM idx WHERE fuzzy(title)")

    def test_fulltext_too_many_args_raises(self):
        """fulltext() with more than 4 arguments raises ValueError."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="at most 4 arguments"):
            parser.parse(
                "SELECT * FROM idx WHERE fulltext(title, 'hello world', 2, true, 'extra')"
            )

    def test_fuzzy_too_many_args_raises(self):
        """fuzzy() with more than 3 arguments raises ValueError."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="at most 3 arguments"):
            parser.parse("SELECT * FROM idx WHERE fuzzy(title, 'laptap', 2, 'extra')")

    def test_fuzzy_non_column_first_arg_raises(self):
        """fuzzy() with non-column first argument raises ValueError."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="must be a column name"):
            parser.parse("SELECT * FROM idx WHERE fuzzy('title', 'laptap')")

    def test_score_non_literal_arg_raises(self):
        """score() with a non-literal argument (e.g., column ref) raises ValueError."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="must be a literal scorer name"):
            parser.parse(
                "SELECT score(my_column) AS relevance FROM idx WHERE fulltext(title, 'laptop')"
            )
