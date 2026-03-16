"""Tests that validate FT.AGGREGATE queries work against Redis 8."""

import struct

import redis


def float_vector_to_bytes(vector: list[float]) -> bytes:
    """Convert a list of floats to binary format for Redis vector storage."""
    return struct.pack(f"{len(vector)}f", *vector)


class TestTextSearchQuery:
    """Test 1: Simple SELECT with WHERE (Text Search)."""

    def test_match_with_price_filter_and_ordering(
        self, redis_client: redis.Redis, products_data: str
    ):
        """Validate FT.AGGREGATE with text match and numeric filter."""
        result = redis_client.execute_command(
            "FT.AGGREGATE",
            products_data,
            "@title:laptop @price:[0 (1000]",
            "LOAD",
            "2",
            "@title",
            "@price",
            "SORTBY",
            "2",
            "@price",
            "ASC",
            "LIMIT",
            "0",
            "10",
            "DIALECT",
            "2",
        )
        # First element is count, rest are results
        assert len(result) > 1, "Should return at least one laptop under $1000"
        # Verify results are sorted by price ascending
        prices = []
        for row in result[1:]:
            row_dict = dict(zip(row[::2], row[1::2]))
            if "price" in row_dict:
                prices.append(float(row_dict["price"]))
        assert prices == sorted(prices), "Results should be sorted by price ASC"


class TestVectorKNNSearch:
    """Test 2: Vector KNN Search."""

    def test_vector_distance_with_knn(self, redis_client: redis.Redis, vec_data: str):
        """Validate FT.AGGREGATE with KNN vector search."""
        query_vector = float_vector_to_bytes([0.1, 0.2, 0.3, 0.4])

        result = redis_client.execute_command(
            "FT.AGGREGATE",
            vec_data,
            "*=>[KNN 5 @embedding $BLOB AS similarity]",
            "LOAD",
            "1",
            "@id",
            "SORTBY",
            "2",
            "@similarity",
            "ASC",
            "LIMIT",
            "0",
            "5",
            "PARAMS",
            "2",
            "BLOB",
            query_vector,
            "DIALECT",
            "2",
        )
        assert len(result) > 1, "Should return vector search results"


class TestHybridSearch:
    """Test 3: Hybrid Search (Text + Vector)."""

    def test_text_and_vector_combined(self, redis_client: redis.Redis, items_data: str):
        """Validate FT.AGGREGATE with hybrid text+vector search."""
        query_vector = float_vector_to_bytes([0.1, 0.2, 0.3, 0.4])

        result = redis_client.execute_command(
            "FT.AGGREGATE",
            items_data,
            "(@category:{electronics} @description:smartphone)=>[KNN 5 @embedding $BLOB AS score]",
            "LOAD",
            "1",
            "@name",
            "SORTBY",
            "2",
            "@score",
            "ASC",
            "LIMIT",
            "0",
            "5",
            "PARAMS",
            "2",
            "BLOB",
            query_vector,
            "DIALECT",
            "2",
        )
        assert len(result) > 1, "Should return hybrid search results"


class TestGroupByWithCount:
    """Test 4: GROUP BY with COUNT."""

    def test_count_with_filter(self, redis_client: redis.Redis, products_data: str):
        """Validate FT.AGGREGATE with GROUPBY, REDUCE COUNT, and FILTER."""
        result = redis_client.execute_command(
            "FT.AGGREGATE",
            products_data,
            "@price:[50 +inf]",
            "GROUPBY",
            "1",
            "@category",
            "REDUCE",
            "COUNT",
            "0",
            "AS",
            "count",
            "FILTER",
            "@count > 0",
            "SORTBY",
            "2",
            "@count",
            "DESC",
            "DIALECT",
            "2",
        )
        assert len(result) > 1, "Should return grouped results"


class TestMultipleAggregations:
    """Test 5: Multiple Aggregations."""

    def test_count_sum_avg_aggregations(
        self, redis_client: redis.Redis, products_data: str
    ):
        """Validate FT.AGGREGATE with multiple REDUCE operations."""
        result = redis_client.execute_command(
            "FT.AGGREGATE",
            products_data,
            "*",
            "GROUPBY",
            "1",
            "@category",
            "REDUCE",
            "COUNT",
            "0",
            "AS",
            "product_count",
            "REDUCE",
            "SUM",
            "1",
            "@price",
            "AS",
            "total_price",
            "REDUCE",
            "AVG",
            "1",
            "@rating",
            "AS",
            "avg_rating",
            "SORTBY",
            "2",
            "@total_price",
            "DESC",
            "LIMIT",
            "0",
            "10",
            "DIALECT",
            "2",
        )
        assert len(result) > 1, "Should return aggregated results"
        # Verify we have the expected fields
        row = dict(zip(result[1][::2], result[1][1::2]))
        assert "product_count" in row
        assert "total_price" in row
        assert "avg_rating" in row


class TestGlobalAggregation:
    """Test 6: Global Aggregation (No GROUP BY)."""

    def test_aggregation_without_group_by(
        self, redis_client: redis.Redis, products_data: str
    ):
        """Validate FT.AGGREGATE with GROUPBY 0 for global aggregation."""
        result = redis_client.execute_command(
            "FT.AGGREGATE",
            products_data,
            "@price:[(100 +inf]",
            "GROUPBY",
            "0",
            "REDUCE",
            "COUNT",
            "0",
            "AS",
            "total_count",
            "REDUCE",
            "AVG",
            "1",
            "@price",
            "AS",
            "avg_price",
            "DIALECT",
            "2",
        )
        assert len(result) == 2, "Should return single aggregation row"
        row = dict(zip(result[1][::2], result[1][1::2]))
        assert "total_count" in row
        assert "avg_price" in row


class TestApplyWithComputedFields:
    """Test 7: APPLY with Computed Fields."""

    def test_computed_field_expression(
        self, redis_client: redis.Redis, products_data: str
    ):
        """Validate FT.AGGREGATE with APPLY for computed fields."""
        result = redis_client.execute_command(
            "FT.AGGREGATE",
            products_data,
            "@price:[(100 +inf]",
            "LOAD",
            "1",
            "@price",
            "APPLY",
            "@price * 0.9",
            "AS",
            "discounted_price",
            "SORTBY",
            "2",
            "@discounted_price",
            "DESC",
            "DIALECT",
            "2",
        )
        assert len(result) > 1, "Should return results with computed field"
        # Verify computed field exists and is 90% of price
        for row in result[1:]:
            row_dict = dict(zip(row[::2], row[1::2]))
            if "price" in row_dict and "discounted_price" in row_dict:
                price = float(row_dict["price"])
                discounted = float(row_dict["discounted_price"])
                assert abs(discounted - price * 0.9) < 0.01


class TestRangeQueryWithBetween:
    """Test 8: Range Query with BETWEEN."""

    def test_between_and_greater_than(
        self, redis_client: redis.Redis, products_data: str
    ):
        """Validate FT.AGGREGATE with numeric range filters."""
        result = redis_client.execute_command(
            "FT.AGGREGATE",
            products_data,
            "@price:[100 500] @stock:[1 +inf]",
            "LOAD",
            "2",
            "@title",
            "@price",
            "DIALECT",
            "2",
        )
        assert len(result) > 1, "Should return products in price range"
        # Verify all prices are in range
        for row in result[1:]:
            row_dict = dict(zip(row[::2], row[1::2]))
            if "price" in row_dict:
                price = float(row_dict["price"])
                assert (
                    100 <= price <= 500
                ), f"Price {price} should be between 100 and 500"


class TestTagFieldMultiValueSearch:
    """Test 9: Tag Field Multi-Value Search."""

    def test_in_clause_with_or(self, redis_client: redis.Redis, products_data: str):
        """Validate FT.AGGREGATE with TAG field OR conditions."""
        result = redis_client.execute_command(
            "FT.AGGREGATE",
            products_data,
            "(@tags:{sale|featured} | @category:{electronics})",
            "LOAD",
            "1",
            "@name",
            "DIALECT",
            "2",
        )
        assert len(result) > 1, "Should return products matching tag OR conditions"


class TestPaginationWithOffset:
    """Test 10: Pagination with OFFSET."""

    def test_limit_with_offset(self, redis_client: redis.Redis, products_data: str):
        """Validate FT.AGGREGATE with LIMIT offset count."""
        # First get all books
        all_books = redis_client.execute_command(
            "FT.AGGREGATE",
            products_data,
            "@category:{books}",
            "LOAD",
            "2",
            "@title",
            "@price",
            "SORTBY",
            "2",
            "@price",
            "DESC",
            "DIALECT",
            "2",
        )

        # Then get with offset (simulating page 2 with page size 1)
        result = redis_client.execute_command(
            "FT.AGGREGATE",
            products_data,
            "@category:{books}",
            "LOAD",
            "2",
            "@title",
            "@price",
            "SORTBY",
            "2",
            "@price",
            "DESC",
            "LIMIT",
            "1",
            "1",  # offset=1, count=1
            "DIALECT",
            "2",
        )

        # Result count doesn't change with LIMIT in FT.AGGREGATE
        assert len(result) >= 1, "Should return paginated results"
        # If we have enough results, verify offset works
        if len(all_books) > 2 and len(result) > 1:
            # Second item from all_books should be first in paginated result
            all_second = dict(zip(all_books[2][::2], all_books[2][1::2]))
            paginated_first = dict(zip(result[1][::2], result[1][1::2]))
            assert all_second.get("title") == paginated_first.get("title")
