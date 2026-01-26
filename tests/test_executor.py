"""Tests for SQL executor."""

import struct

import pytest
import redis
from testcontainers.redis import RedisContainer

from sql_redis.executor import Executor, QueryResult
from sql_redis.schema import SchemaRegistry


@pytest.fixture(scope="module")
def redis_container():
    """Start a Redis container for testing."""
    with RedisContainer("redis:8.0.2") as container:
        yield container


@pytest.fixture(scope="module")
def redis_client(redis_container) -> redis.Redis:
    """Create a Redis client connected to the test container."""
    client = redis.Redis(
        host=redis_container.get_container_host_ip(),
        port=redis_container.get_exposed_port(6379),
        decode_responses=True,
    )
    return client


@pytest.fixture
def products_index(redis_client: redis.Redis) -> str:
    """Create a products index with test data."""
    index_name = "products"
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
        "product:",
        "SCHEMA",
        "title",
        "TEXT",
        "category",
        "TAG",
        "price",
        "NUMERIC",
        "stock",
        "NUMERIC",
    )

    # Add test data
    redis_client.hset(
        "product:1",
        mapping={
            "title": "Laptop Pro",
            "category": "electronics",
            "price": "999.99",
            "stock": "10",
        },
    )
    redis_client.hset(
        "product:2",
        mapping={
            "title": "Wireless Mouse",
            "category": "electronics",
            "price": "29.99",
            "stock": "50",
        },
    )
    redis_client.hset(
        "product:3",
        mapping={
            "title": "Python Book",
            "category": "books",
            "price": "49.99",
            "stock": "25",
        },
    )
    redis_client.hset(
        "product:4",
        mapping={
            "title": "Redis Guide",
            "category": "books",
            "price": "39.99",
            "stock": "15",
        },
    )

    return index_name


@pytest.fixture
def executor(redis_client: redis.Redis, products_index: str) -> Executor:
    """Create an executor with the products index loaded."""
    registry = SchemaRegistry(redis_client)
    registry.load_all()
    return Executor(redis_client, registry)


class TestQueryResult:
    """Tests for QueryResult structure."""

    def test_query_result_has_rows(self, executor: Executor, products_index: str):
        """QueryResult has rows attribute."""
        result = executor.execute(f"SELECT * FROM {products_index}")
        assert hasattr(result, "rows")
        assert isinstance(result.rows, list)

    def test_query_result_has_count(self, executor: Executor, products_index: str):
        """QueryResult has count attribute."""
        result = executor.execute(f"SELECT * FROM {products_index}")
        assert hasattr(result, "count")
        assert isinstance(result.count, int)

    def test_query_result_rows_are_dicts(self, executor: Executor, products_index: str):
        """Rows are dictionaries."""
        result = executor.execute(f"SELECT * FROM {products_index}")
        assert len(result.rows) > 0
        assert isinstance(result.rows[0], dict)


class TestBasicExecute:
    """Tests for basic query execution."""

    def test_select_all(self, executor: Executor, products_index: str):
        """SELECT * returns all documents."""
        result = executor.execute(f"SELECT * FROM {products_index}")
        assert result.count == 4
        assert len(result.rows) == 4

    def test_select_with_text_filter(self, executor: Executor, products_index: str):
        """SELECT with text filter."""
        result = executor.execute(
            f"SELECT * FROM {products_index} WHERE title = 'laptop'"
        )
        assert result.count >= 1
        assert any("Laptop" in row.get("title", "") for row in result.rows)

    def test_select_with_numeric_filter(self, executor: Executor, products_index: str):
        """SELECT with numeric comparison."""
        result = executor.execute(f"SELECT * FROM {products_index} WHERE price < 50")
        assert result.count >= 2
        for row in result.rows:
            assert float(row["price"]) < 50

    def test_select_with_tag_filter(self, executor: Executor, products_index: str):
        """SELECT with tag filter."""
        result = executor.execute(
            f"SELECT * FROM {products_index} WHERE category = 'books'"
        )
        assert result.count == 2
        for row in result.rows:
            assert row["category"] == "books"

    def test_select_with_limit(self, executor: Executor, products_index: str):
        """SELECT with LIMIT."""
        result = executor.execute(f"SELECT * FROM {products_index} LIMIT 2")
        assert len(result.rows) == 2

    def test_select_with_order_by(self, executor: Executor, products_index: str):
        """SELECT with ORDER BY."""
        result = executor.execute(f"SELECT * FROM {products_index} ORDER BY price DESC")
        prices = [float(row["price"]) for row in result.rows]
        assert prices == sorted(prices, reverse=True)


class TestAggregateExecute:
    """Tests for aggregate query execution."""

    def test_count_all(self, executor: Executor, products_index: str):
        """SELECT COUNT(*) returns count."""
        result = executor.execute(f"SELECT COUNT(*) FROM {products_index}")
        assert len(result.rows) == 1
        # COUNT(*) should be in the result
        row = result.rows[0]
        count_value = row.get("COUNT(*)", row.get("count", None))
        assert count_value is not None

    def test_group_by_with_count(self, executor: Executor, products_index: str):
        """SELECT with GROUP BY and COUNT."""
        result = executor.execute(
            f"SELECT category, COUNT(*) as cnt FROM {products_index} GROUP BY category"
        )
        assert len(result.rows) == 2  # electronics and books
        categories = {row["category"] for row in result.rows}
        assert categories == {"electronics", "books"}

    def test_sum_aggregation(self, executor: Executor, products_index: str):
        """SELECT SUM(field) returns sum."""
        result = executor.execute(f"SELECT SUM(price) AS total FROM {products_index}")
        assert len(result.rows) == 1
        total = float(result.rows[0]["total"])
        expected = 999.99 + 29.99 + 49.99 + 39.99
        assert abs(total - expected) < 0.01


class TestExecuteWithParams:
    """Tests for parameterized execution."""

    def test_numeric_param(self, executor: Executor, products_index: str):
        """Execute with numeric parameter."""
        result = executor.execute(
            f"SELECT * FROM {products_index} WHERE price > :min_price",
            params={"min_price": 40},
        )
        for row in result.rows:
            assert float(row["price"]) > 40

    def test_string_param(self, executor: Executor, products_index: str):
        """Execute with string parameter."""
        result = executor.execute(
            f"SELECT * FROM {products_index} WHERE category = :cat",
            params={"cat": "books"},
        )
        assert len(result.rows) == 2
        for row in result.rows:
            assert row["category"] == "books"


class TestVectorSearch:
    """Tests for vector search execution."""

    @pytest.fixture
    def vector_index(self, redis_client: redis.Redis) -> str:
        """Create a vector index with test data."""
        index_name = "vectors"
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
            "4",
            "DISTANCE_METRIC",
            "COSINE",
        )

        def to_bytes(v):
            return struct.pack(f"{len(v)}f", *v)

        # Use redis client without decode_responses for binary data
        raw_client = redis.Redis(
            host=redis_client.connection_pool.connection_kwargs["host"],
            port=redis_client.connection_pool.connection_kwargs["port"],
            decode_responses=False,
        )
        raw_client.hset(
            "vec:1",
            mapping={"title": "First", "embedding": to_bytes([0.1, 0.2, 0.3, 0.4])},
        )
        raw_client.hset(
            "vec:2",
            mapping={"title": "Second", "embedding": to_bytes([0.5, 0.6, 0.7, 0.8])},
        )
        raw_client.hset(
            "vec:3",
            mapping={"title": "Third", "embedding": to_bytes([0.9, 0.8, 0.7, 0.6])},
        )

        return index_name

    def test_vector_search_with_param(
        self, redis_client: redis.Redis, vector_index: str
    ):
        """Vector search with vector parameter."""
        registry = SchemaRegistry(redis_client)
        registry.load_all()
        executor = Executor(redis_client, registry)

        query_vector = struct.pack("4f", 0.1, 0.2, 0.3, 0.4)
        result = executor.execute(
            f"SELECT title, vector_distance(embedding, :vec) AS score "
            f"FROM {vector_index} LIMIT 3",
            params={"vec": query_vector},
        )
        assert len(result.rows) <= 3
        # First result should be closest to query vector
        assert result.rows[0]["title"] == "First"


class TestErrorHandling:
    """Tests for error handling."""

    def test_invalid_sql_raises(self, executor: Executor):
        """Invalid SQL raises exception."""
        with pytest.raises(Exception):
            executor.execute("NOT VALID SQL")

    def test_unknown_index_raises(self, executor: Executor):
        """Unknown index raises exception."""
        with pytest.raises(Exception):
            executor.execute("SELECT * FROM nonexistent_index")
