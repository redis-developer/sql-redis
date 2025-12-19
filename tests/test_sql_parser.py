"""Tests for the SQL parser component."""

import pytest

from sql_redis.parser import SQLParser, ParsedQuery


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
        result = parser.parse(
            "SELECT price, (price * 0.9) AS discounted FROM products"
        )
        
        assert len(result.computed_fields) == 1
        assert result.computed_fields[0].expression == "price * 0.9"
        assert result.computed_fields[0].alias == "discounted"

    def test_parse_vector_distance_function(self):
        """Parse SELECT with vector_distance function."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT id, vector_distance(embedding, $vector) AS similarity FROM vectors"
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
        result = parser.parse("SELECT * FROM products WHERE tags IN ('sale', 'featured')")
        
        assert result.conditions[0].field == "tags"
        assert result.conditions[0].operator == "IN"
        assert result.conditions[0].value == ["sale", "featured"]

    def test_parse_match_function(self):
        """Parse WHERE with MATCH function."""
        parser = SQLParser()
        result = parser.parse("SELECT * FROM products WHERE MATCH(title, 'laptop')")
        
        assert result.conditions[0].field == "title"
        assert result.conditions[0].operator == "MATCH"
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

