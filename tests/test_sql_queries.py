"""Integration tests: SQL queries producing same results as raw Redis queries."""

import struct

import pytest
import redis

from sql_redis.executor import Executor
from sql_redis.schema import SchemaRegistry


def float_vector_to_bytes(vector: list[float]) -> bytes:
    """Convert a list of floats to binary format for Redis vector storage."""
    return struct.pack(f"{len(vector)}f", *vector)


@pytest.fixture(scope="module")
def schema_registry(redis_client: redis.Redis, products_data, vec_data, items_data):
    """Create schema registry after all indexes and data are loaded."""
    registry = SchemaRegistry(redis_client)
    registry.load_all()
    return registry


@pytest.fixture(scope="module")
def executor(redis_client: redis.Redis, schema_registry: SchemaRegistry):
    """Create executor with schema registry."""
    return Executor(redis_client, schema_registry)


class TestTextSearchQuery:
    """Test 1: Simple SELECT with WHERE (Text Search)."""

    def test_match_with_price_filter_and_ordering(
        self, executor: Executor, products_data: str
    ):
        """SQL equivalent of FT.AGGREGATE with text match and numeric filter."""
        # For TEXT fields, = becomes a text search in Redis
        result = executor.execute(f"""
            SELECT title, price
            FROM {products_data}
            WHERE title = 'laptop' AND price < 1000
            ORDER BY price ASC
            LIMIT 10
            """)
        assert len(result.rows) >= 1, "Should return at least one laptop under $1000"
        # Verify results are sorted by price ascending
        prices = [float(row["price"]) for row in result.rows]
        assert prices == sorted(prices), "Results should be sorted by price ASC"


class TestVectorKNNSearch:
    """Test 2: Vector KNN Search."""

    def test_vector_distance_with_knn(self, executor: Executor, vec_data: str):
        """SQL equivalent of FT.AGGREGATE with KNN vector search."""
        query_vector = float_vector_to_bytes([0.1, 0.2, 0.3, 0.4])

        result = executor.execute(
            f"""
            SELECT id, vector_distance(embedding, :vec) AS similarity
            FROM {vec_data}
            LIMIT 5
            """,
            params={"vec": query_vector},
        )
        assert len(result.rows) >= 1, "Should return vector search results"


class TestHybridSearch:
    """Test 3: Hybrid Search (Text + Vector)."""

    def test_text_and_vector_combined(self, executor: Executor, items_data: str):
        """SQL equivalent of FT.AGGREGATE with hybrid text+vector search."""
        query_vector = float_vector_to_bytes([0.1, 0.2, 0.3, 0.4])

        # For TEXT fields, = becomes a text search in Redis
        result = executor.execute(
            f"""
            SELECT name, vector_distance(embedding, :vec) AS score
            FROM {items_data}
            WHERE category = 'electronics' AND description = 'smartphone'
            LIMIT 5
            """,
            params={"vec": query_vector},
        )
        assert len(result.rows) >= 1, "Should return hybrid search results"


class TestGroupByWithCount:
    """Test 4: GROUP BY with COUNT."""

    def test_count_with_filter(self, executor: Executor, products_data: str):
        """SQL equivalent of FT.AGGREGATE with GROUPBY and COUNT."""
        result = executor.execute(f"""
            SELECT category, COUNT(*) AS count
            FROM {products_data}
            WHERE price >= 50
            GROUP BY category
            ORDER BY count DESC
            """)
        assert len(result.rows) >= 1, "Should return grouped results"
        for row in result.rows:
            assert "category" in row
            assert "count" in row


class TestMultipleAggregations:
    """Test 5: Multiple Aggregations."""

    def test_count_sum_avg_aggregations(self, executor: Executor, products_data: str):
        """SQL equivalent of FT.AGGREGATE with multiple REDUCE operations."""
        result = executor.execute(f"""
            SELECT category, COUNT(*) AS product_count, SUM(price) AS total_price, AVG(rating) AS avg_rating
            FROM {products_data}
            GROUP BY category
            ORDER BY total_price DESC
            LIMIT 10
            """)
        assert len(result.rows) >= 1, "Should return aggregated results"
        row = result.rows[0]
        assert "product_count" in row
        assert "total_price" in row
        assert "avg_rating" in row


class TestGlobalAggregation:
    """Test 6: Global Aggregation (No GROUP BY)."""

    def test_aggregation_without_group_by(self, executor: Executor, products_data: str):
        """SQL equivalent of FT.AGGREGATE with GROUPBY 0 for global aggregation."""
        result = executor.execute(f"""
            SELECT COUNT(*) AS total_count, AVG(price) AS avg_price
            FROM {products_data}
            WHERE price > 100
            """)
        assert len(result.rows) == 1, "Should return single aggregation row"
        row = result.rows[0]
        assert "total_count" in row
        assert "avg_price" in row


class TestApplyWithComputedFields:
    """Test 7: APPLY with Computed Fields."""

    def test_computed_field_expression(self, executor: Executor, products_data: str):
        """SQL equivalent of FT.AGGREGATE with APPLY for computed fields."""
        result = executor.execute(f"""
            SELECT price, price * 0.9 AS discounted_price
            FROM {products_data}
            WHERE price > 100
            ORDER BY discounted_price DESC
            """)
        assert len(result.rows) >= 1, "Should return results with computed field"
        for row in result.rows:
            price = float(row["price"])
            discounted = float(row["discounted_price"])
            assert abs(discounted - price * 0.9) < 0.01

    def test_computed_field_addition(self, executor: Executor, products_data: str):
        """Computed field with addition."""
        result = executor.execute(f"""
            SELECT price, price + 10 AS price_with_shipping
            FROM {products_data}
            WHERE price < 200
            LIMIT 5
            """)
        assert len(result.rows) >= 1
        for row in result.rows:
            price = float(row["price"])
            with_shipping = float(row["price_with_shipping"])
            assert abs(with_shipping - (price + 10)) < 0.01

    def test_computed_field_division(self, executor: Executor, products_data: str):
        """Computed field with division for percentage."""
        result = executor.execute(f"""
            SELECT price, price / 100 AS price_in_hundreds
            FROM {products_data}
            WHERE price >= 100
            LIMIT 5
            """)
        assert len(result.rows) >= 1
        for row in result.rows:
            price = float(row["price"])
            in_hundreds = float(row["price_in_hundreds"])
            assert abs(in_hundreds - price / 100) < 0.01

    def test_multiple_computed_fields(self, executor: Executor, products_data: str):
        """Multiple computed fields in one query."""
        result = executor.execute(f"""
            SELECT price, price * 0.9 AS sale_price, price * 1.1 AS markup_price
            FROM {products_data}
            WHERE price > 50
            LIMIT 5
            """)
        assert len(result.rows) >= 1
        for row in result.rows:
            price = float(row["price"])
            sale = float(row["sale_price"])
            markup = float(row["markup_price"])
            assert abs(sale - price * 0.9) < 0.01
            assert abs(markup - price * 1.1) < 0.01


class TestRangeQueryWithBetween:
    """Test 8: Range Query with BETWEEN."""

    def test_between_and_greater_than(self, executor: Executor, products_data: str):
        """SQL equivalent of FT.AGGREGATE with numeric range filters."""
        result = executor.execute(f"""
            SELECT title, price
            FROM {products_data}
            WHERE price BETWEEN 100 AND 500 AND stock >= 1
            """)
        assert len(result.rows) >= 1, "Should return products in price range"
        for row in result.rows:
            price = float(row["price"])
            assert 100 <= price <= 500, f"Price {price} should be between 100 and 500"


class TestTagFieldMultiValueSearch:
    """Test 9: Tag Field Multi-Value Search."""

    def test_in_clause_with_or(self, executor: Executor, products_data: str):
        """SQL equivalent of FT.AGGREGATE with TAG field OR conditions."""
        result = executor.execute(f"""
            SELECT name
            FROM {products_data}
            WHERE tags IN ('sale', 'featured') OR category = 'electronics'
            """)
        assert (
            len(result.rows) >= 1
        ), "Should return products matching tag OR conditions"

    def test_tag_in_clause_only(self, executor: Executor, products_data: str):
        """IN clause on TAG field without OR."""
        result = executor.execute(f"""
            SELECT name, category
            FROM {products_data}
            WHERE category IN ('electronics', 'books')
            """)
        assert len(result.rows) >= 1
        for row in result.rows:
            assert row["category"] in ["electronics", "books"]

    def test_tag_equality(self, executor: Executor, products_data: str):
        """Simple TAG equality filter."""
        result = executor.execute(f"""
            SELECT name, category
            FROM {products_data}
            WHERE category = 'electronics'
            """)
        assert len(result.rows) >= 1
        for row in result.rows:
            assert row["category"] == "electronics"

    def test_or_with_same_field_type(self, executor: Executor, products_data: str):
        """OR condition across same field type (TAG)."""
        result = executor.execute(f"""
            SELECT name, category
            FROM {products_data}
            WHERE category = 'electronics' OR category = 'books'
            """)
        assert len(result.rows) >= 1
        for row in result.rows:
            assert row["category"] in ["electronics", "books"]

    def test_or_with_different_field_types(
        self, executor: Executor, products_data: str
    ):
        """OR condition across different field types (TAG and NUMERIC)."""
        result = executor.execute(f"""
            SELECT name, category, price
            FROM {products_data}
            WHERE category = 'books' OR price > 800
            """)
        assert len(result.rows) >= 1
        for row in result.rows:
            is_book = row["category"] == "books"
            is_expensive = float(row["price"]) > 800
            assert is_book or is_expensive, f"Row should match OR: {row}"


class TestPaginationWithOffset:
    """Test 10: Pagination with OFFSET."""

    def test_limit_with_offset(self, executor: Executor, products_data: str):
        """SQL equivalent of FT.AGGREGATE with LIMIT offset count."""
        # First get all books sorted
        all_books = executor.execute(f"""
            SELECT title, price
            FROM {products_data}
            WHERE category = 'books'
            ORDER BY price DESC
            """)

        # Then get with offset (page 2, page size 1)
        paginated = executor.execute(f"""
            SELECT title, price
            FROM {products_data}
            WHERE category = 'books'
            ORDER BY price DESC
            LIMIT 1 OFFSET 1
            """)

        assert len(paginated.rows) >= 1, "Should return paginated results"
        # If we have enough results, verify offset works
        if len(all_books.rows) > 1 and len(paginated.rows) >= 1:
            # Second item from all_books should be first in paginated result
            assert all_books.rows[1]["title"] == paginated.rows[0]["title"]


class TestFuzzySearch:
    """Integration tests for fuzzy text search with Levenshtein distance levels."""

    def test_fuzzy_ld1_finds_misspelled(self, executor: Executor, products_data: str):
        """fuzzy(field, 'laptap') at LD=1 should find 'laptop' titles."""
        result = executor.execute(
            f"SELECT title FROM {products_data} WHERE fuzzy(title, 'laptap')"
        )
        assert len(result.rows) >= 1, "Fuzzy LD=1 should match 'laptop' from 'laptap'"
        for row in result.rows:
            assert "laptop" in row["title"].lower()

    def test_fuzzy_ld2(self, executor: Executor, products_data: str):
        """fuzzy(field, 'laptep', 2) at LD=2 should still find 'laptop'."""
        result = executor.execute(
            f"SELECT title FROM {products_data} WHERE fuzzy(title, 'laptep', 2)"
        )
        assert len(result.rows) >= 1, "Fuzzy LD=2 should match 'laptop' from 'laptep'"

    def test_fuzzy_ld3(self, executor: Executor, products_data: str):
        """fuzzy(field, 'loptep', 3) at LD=3 should find 'laptop'."""
        result = executor.execute(
            f"SELECT title FROM {products_data} WHERE fuzzy(title, 'loptep', 3)"
        )
        assert len(result.rows) >= 1, "Fuzzy LD=3 should match 'laptop' from 'loptep'"


class TestSuffixInfixSearch:
    """Integration tests for suffix and infix (contains) pattern matching."""

    def test_prefix_search(self, executor: Executor, products_data: str):
        """LIKE 'lap%' should find laptop titles (prefix match)."""
        result = executor.execute(
            f"SELECT title FROM {products_data} WHERE title LIKE 'lap%'"
        )
        assert len(result.rows) >= 1, "Prefix 'lap%' should match laptop titles"

    def test_suffix_search(self, executor: Executor, products_data: str):
        """LIKE '%board' should find keyboard titles (suffix match)."""
        result = executor.execute(
            f"SELECT title FROM {products_data} WHERE title LIKE '%board'"
        )
        # "Mechanical Keyboard" has 'board' at end of 'Keyboard'
        assert len(result.rows) >= 1, "Suffix '%board' should match 'Keyboard'"

    def test_infix_search(self, executor: Executor, products_data: str):
        """LIKE '%ouse%' should find 'Wireless Mouse' (contains match)."""
        result = executor.execute(
            f"SELECT title FROM {products_data} WHERE title LIKE '%ouse%'"
        )
        assert len(result.rows) >= 1, "Infix '%ouse%' should match 'Mouse'"


class TestORInTextSearch:
    """Integration tests for OR/union within text field searches."""

    def test_fulltext_or_two_terms(self, executor: Executor, products_data: str):
        """fulltext(field, 'laptop OR keyboard') should find both."""
        result = executor.execute(
            f"SELECT title FROM {products_data} WHERE fulltext(title, 'laptop OR keyboard')"
        )
        titles = [row["title"].lower() for row in result.rows]
        has_laptop = any("laptop" in t for t in titles)
        has_keyboard = any("keyboard" in t for t in titles)
        assert (
            has_laptop and has_keyboard
        ), f"Should find both laptop and keyboard titles, got: {titles}"

    def test_fulltext_or_three_terms(self, executor: Executor, products_data: str):
        """fulltext(field, 'laptop OR mouse OR lamp') should find all three."""
        result = executor.execute(
            f"SELECT title FROM {products_data} WHERE fulltext(title, 'laptop OR mouse OR lamp')"
        )
        assert (
            len(result.rows) >= 3
        ), f"Should find at least 3 products (laptop, mouse, lamp), got {len(result.rows)}"


class TestProximitySearch:
    """Integration tests for proximity search (slop + inorder)."""

    def test_fulltext_with_slop(self, executor: Executor, products_data: str):
        """fulltext(title, 'gaming pro', 2) should find 'Gaming laptop Pro'."""
        result = executor.execute(
            f"SELECT title FROM {products_data} WHERE fulltext(title, 'gaming pro', 2)"
        )
        assert (
            len(result.rows) >= 1
        ), "Slop=2 should find 'Gaming laptop Pro' (1 word between gaming and pro)"

    def test_fulltext_with_slop_and_inorder(
        self, executor: Executor, products_data: str
    ):
        """fulltext(title, 'gaming pro', 2, true) with inorder should match."""
        result = executor.execute(
            f"SELECT title FROM {products_data} WHERE fulltext(title, 'gaming pro', 2, true)"
        )
        assert (
            len(result.rows) >= 1
        ), "Slop=2 with inorder should find 'Gaming laptop Pro'"


class TestBM25Scoring:
    """Integration tests for relevance scoring with WITHSCORES."""

    def test_score_returns_relevance(self, executor: Executor, products_data: str):
        """score() in SELECT should return relevance scores."""
        result = executor.execute(f"""SELECT title, score() AS relevance
            FROM {products_data}
            WHERE fulltext(title, 'laptop')""")
        assert len(result.rows) >= 1, "Should return results with scores"
        for row in result.rows:
            assert "relevance" in row, f"Row should have 'relevance' key: {row}"
            score = float(row["relevance"])
            assert score >= 0, f"Score should be non-negative, got {score}"

    def test_score_custom_scorer(self, executor: Executor, products_data: str):
        """score('TFIDF') should use TFIDF scorer."""
        result = executor.execute(f"""SELECT title, score('TFIDF') AS relevance
            FROM {products_data}
            WHERE fulltext(title, 'laptop')""")
        assert len(result.rows) >= 1, "Should return results with TFIDF scores"
        for row in result.rows:
            assert "relevance" in row
            score = float(row["relevance"])
            assert score >= 0
