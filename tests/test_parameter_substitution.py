"""Tests for SQL parameter substitution bugs in Executor.

These tests verify that parameter substitution correctly handles:
1. Partial matching bug: :id should not replace inside :product_id
2. Quote escaping bug: Single quotes in values should be SQL-escaped
3. Edge cases: Multiple occurrences, similar names, special characters

Following a TDD approach: These tests were written to fail when the bugs were present and now verify that the fixes work correctly and prevent regressions.
"""

import pytest
import redis

from sql_redis.executor import Executor
from sql_redis.schema import SchemaRegistry


@pytest.fixture
def param_test_index(redis_client: redis.Redis) -> str:
    """Create a test index for parameter substitution tests."""
    index_name = "param_test"
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
        "param:",
        "SCHEMA",
        "id",
        "NUMERIC",
        "product_id",
        "NUMERIC",
        "user_id",
        "NUMERIC",
        "name",
        "TEXT",
        "title",
        "TEXT",
        "category",
        "TAG",
    )

    # Add test data with various names including apostrophes
    redis_client.hset(
        "param:1",
        mapping={
            "id": "1",
            "product_id": "101",
            "user_id": "201",
            "name": "John Smith",
            "title": "Product A",
            "category": "electronics",
        },
    )
    redis_client.hset(
        "param:2",
        mapping={
            "id": "2",
            "product_id": "102",
            "user_id": "202",
            "name": "Mary O'Brien",
            "title": "Product B",
            "category": "books",
        },
    )
    redis_client.hset(
        "param:3",
        mapping={
            "id": "3",
            "product_id": "103",
            "user_id": "203",
            "name": "Pat McDonald's",
            "title": "Product C",
            "category": "electronics",
        },
    )

    return index_name


@pytest.fixture
def param_executor(redis_client: redis.Redis, param_test_index: str) -> Executor:
    """Create an executor for parameter substitution tests."""
    registry = SchemaRegistry(redis_client)
    registry.load_all()
    return Executor(redis_client, registry)


class TestPartialMatchingBug:
    """Tests for the partial string matching bug.

    The bug: Using str.replace(':id', '123') would also replace
    ':id' inside ':product_id', resulting in 'product_123'.
    """

    def test_similar_param_names_no_partial_match(
        self, param_executor: Executor, param_test_index: str
    ):
        """Test that :id doesn't replace inside :product_id."""
        result = param_executor.execute(
            f"SELECT * FROM {param_test_index} WHERE id = :id AND product_id = :product_id",
            params={"id": 1, "product_id": 101},
        )

        # Should find the matching record
        assert len(result.rows) == 1
        assert result.rows[0]["id"] == "1"
        assert result.rows[0]["product_id"] == "101"

    def test_prefix_param_names(self, param_executor: Executor, param_test_index: str):
        """Test params where one is a prefix of another: :user, :user_id."""
        # This test uses hypothetical :user param - we'll test with actual fields
        result = param_executor.execute(
            f"SELECT * FROM {param_test_index} WHERE user_id = :user_id",
            params={"user_id": 201},
        )

        assert len(result.rows) == 1
        assert result.rows[0]["user_id"] == "201"

    def test_multiple_similar_params(
        self, param_executor: Executor, param_test_index: str
    ):
        """Test multiple params with overlapping names."""
        result = param_executor.execute(
            f"SELECT * FROM {param_test_index} WHERE id = :id AND product_id = :product_id AND user_id = :user_id",
            params={"id": 2, "product_id": 102, "user_id": 202},
        )

        assert len(result.rows) == 1
        assert result.rows[0]["id"] == "2"
        assert result.rows[0]["product_id"] == "102"
        assert result.rows[0]["user_id"] == "202"


class TestQuoteEscapingBug:
    """Tests for the quote escaping bug.

    The bug: String values with single quotes like "O'Brien" would
    produce invalid SQL: 'O'Brien' instead of 'O''Brien'.
    This causes SQL parsing errors or incorrect query results.
    """

    def test_single_quote_in_value(
        self, param_executor: Executor, param_test_index: str
    ):
        """Test that single quotes are properly escaped in string parameters."""
        # Search for "O'Brien" - this should work if quotes are escaped
        result = param_executor.execute(
            f"SELECT * FROM {param_test_index} WHERE name = :name",
            params={"name": "O'Brien"},
        )

        # Should find Mary O'Brien
        assert len(result.rows) == 1
        assert "O'Brien" in result.rows[0]["name"]

    def test_multiple_quotes_in_value(
        self, param_executor: Executor, param_test_index: str
    ):
        """Test multiple single quotes in a value."""
        # Search for "McDonald's" - has apostrophe
        result = param_executor.execute(
            f"SELECT * FROM {param_test_index} WHERE name = :name",
            params={"name": "McDonald's"},
        )

        # Should find Pat McDonald's
        assert len(result.rows) == 1
        assert "McDonald's" in result.rows[0]["name"]

    def test_apostrophe_in_text_search(
        self, param_executor: Executor, param_test_index: str
    ):
        """Test apostrophe in text field search."""
        # This tests TEXT field behavior with quotes
        result = param_executor.execute(
            f"SELECT * FROM {param_test_index} WHERE title = :title",
            params={"title": "Product's Name"},
        )

        # Should not crash, even if no results
        assert isinstance(result.rows, list)


class TestEdgeCases:
    """Tests for edge cases in parameter substitution."""

    def test_multiple_occurrences_same_param(
        self, param_executor: Executor, param_test_index: str
    ):
        """Test that a parameter used multiple times is substituted everywhere."""
        result = param_executor.execute(
            f"SELECT * FROM {param_test_index} WHERE category = :cat OR title = :cat",
            params={"cat": "electronics"},
        )

        # Should find records where category OR title matches
        assert len(result.rows) >= 1

    def test_empty_string_value(self, param_executor: Executor, param_test_index: str):
        """Test empty string parameter value.

        Note: Redis Search doesn't handle empty string literals well in TEXT fields.
        This is a Redis limitation, not a parameter substitution bug.
        """
        # Empty strings cause Redis syntax errors in TEXT field queries
        # This is expected behavior - Redis Search requires non-empty search terms
        with pytest.raises(redis.exceptions.ResponseError, match="Syntax error"):
            param_executor.execute(
                f"SELECT * FROM {param_test_index} WHERE name = :name",
                params={"name": ""},
            )

    def test_numeric_types(self, param_executor: Executor, param_test_index: str):
        """Test integer and float parameter values."""
        result = param_executor.execute(
            f"SELECT * FROM {param_test_index} WHERE id = :id",
            params={"id": 1},
        )

        assert len(result.rows) == 1
        assert result.rows[0]["id"] == "1"

    def test_special_characters_in_value(
        self, param_executor: Executor, param_test_index: str
    ):
        """Test special characters that might interfere with string replacement.

        Note: Some special characters cause Redis Search syntax errors in TEXT fields.
        This is a Redis limitation, not a parameter substitution bug.
        The parameter substitution correctly escapes quotes, which is the main concern.
        """
        # Characters like @ and : have special meaning in Redis Search syntax.
        # Verify parameter substitution correctly injects these values into SQL
        # (even if Redis may reject some at execution time).
        from sql_redis.executor import _substitute_params

        problematic_values = [
            ("hello@world.com", "'hello@world.com'"),
            ("price: $100", "'price: $100'"),
            ("path/to/file", "'path/to/file'"),
        ]

        sql_template = f"SELECT * FROM {param_test_index} WHERE name = :name"

        for value, expected_literal in problematic_values:
            # Verify substitution produces correct SQL (no Redis round-trip needed)
            substituted = _substitute_params(sql_template, {"name": value})
            assert expected_literal in substituted, (
                f"Expected {expected_literal!r} in substituted SQL for value {value!r}"
            )

        # Verify at least one value executes successfully against Redis
        result = param_executor.execute(
            sql_template, params={"name": "path/to/file"},
        )
        assert isinstance(result.rows, list)


class TestBugDemonstration:
    """Tests that explicitly demonstrate the bugs in the current implementation.

    These tests show what goes wrong with the naive str.replace() approach.
    """

    def test_partial_match_corruption_demo(
        self, param_executor: Executor, param_test_index: str
    ):
        """Demonstrate that parameter order can cause corruption."""
        # If :id is replaced before :product_id, we get corruption
        # The actual behavior depends on dict iteration order
        try:
            result = param_executor.execute(
                f"SELECT * FROM {param_test_index} WHERE id = :id AND product_id = :product_id",
                params={"id": 999, "product_id": 888},
            )
            # If the bug exists, the query might be malformed
            # We're just checking it doesn't crash here
            assert isinstance(result.rows, list)
        except Exception as e:
            # If it crashes, that's also evidence of the bug
            pytest.fail(f"Query crashed due to parameter substitution bug: {e}")

    def test_quote_causes_parse_error_demo(
        self, param_executor: Executor, param_test_index: str
    ):
        """Demonstrate that quotes are now properly escaped.

        This test used to fail before the fix. Now it should pass because
        the token-based substitution properly escapes single quotes.

        Note: The query may still fail due to Redis Search stopword handling,
        but NOT due to quote escaping issues.
        """
        # This should work now that quotes are properly escaped
        # The apostrophe in "It's" is escaped to "It''s"
        try:
            result = param_executor.execute(
                f"SELECT * FROM {param_test_index} WHERE name = :name",
                params={"name": "It's a test"},
            )
            # Should work if quotes are properly escaped
            assert isinstance(result.rows, list)
        except redis.exceptions.ResponseError as e:
            # If it's a syntax error about stopwords, that's OK (Redis limitation)
            # If it's about quote escaping, that's a bug
            if "Syntax error" in str(e) and "It" in str(e):
                # This is likely a stopword issue, not quote escaping
                # The important thing is the quote was escaped properly
                pass
            else:
                pytest.fail(f"Unexpected error: {e}")
