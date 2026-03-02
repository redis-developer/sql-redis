"""Integration tests for async SQL executor.

TDD: These tests define the expected behavior for AsyncSchemaRegistry and AsyncExecutor.
"""

import struct

import pytest
import redis.asyncio as async_redis
from testcontainers.redis import RedisContainer

from sql_redis.executor import AsyncExecutor, QueryResult
from sql_redis.schema import AsyncSchemaRegistry


@pytest.fixture(scope="module")
def redis_container():
    """Start a Redis container for testing."""
    with RedisContainer("redis:8.0.2") as container:
        yield container


@pytest.fixture
async def async_client(redis_container) -> async_redis.Redis:
    """Create an async Redis client connected to the test container."""
    client = async_redis.Redis(
        host=redis_container.get_container_host_ip(),
        port=int(redis_container.get_exposed_port(6379)),
        decode_responses=True,
    )
    yield client
    await client.aclose()


@pytest.fixture
async def products_index(async_client: async_redis.Redis) -> str:
    """Create a products index with test data."""
    index_name = "async_products"
    try:
        await async_client.execute_command("FT.DROPINDEX", index_name, "DD")
    except Exception:
        pass

    await async_client.execute_command(
        "FT.CREATE",
        index_name,
        "ON",
        "HASH",
        "PREFIX",
        "1",
        "async_product:",
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
    await async_client.hset(
        "async_product:1",
        mapping={
            "title": "Laptop Pro",
            "category": "electronics",
            "price": "999.99",
            "stock": "10",
        },
    )
    await async_client.hset(
        "async_product:2",
        mapping={
            "title": "Wireless Mouse",
            "category": "electronics",
            "price": "29.99",
            "stock": "50",
        },
    )
    await async_client.hset(
        "async_product:3",
        mapping={
            "title": "Python Book",
            "category": "books",
            "price": "49.99",
            "stock": "25",
        },
    )
    await async_client.hset(
        "async_product:4",
        mapping={
            "title": "Redis Guide",
            "category": "books",
            "price": "39.99",
            "stock": "15",
        },
    )

    yield index_name

    # Cleanup
    try:
        await async_client.execute_command("FT.DROPINDEX", index_name, "DD")
    except Exception:
        pass


@pytest.fixture
async def async_executor(
    async_client: async_redis.Redis, products_index: str
) -> AsyncExecutor:
    """Create an async executor with the products index loaded."""
    registry = AsyncSchemaRegistry(async_client)
    await registry.load_all()
    return AsyncExecutor(async_client, registry)


class TestAsyncSchemaRegistry:
    """Tests for AsyncSchemaRegistry."""

    async def test_load_all_loads_indexes(
        self, async_client: async_redis.Redis, products_index: str
    ):
        """load_all() should load index schemas from Redis."""
        registry = AsyncSchemaRegistry(async_client)
        await registry.load_all()

        schema = registry.get_schema(products_index)
        assert schema is not None
        assert "title" in schema
        assert schema["title"] == "TEXT"
        assert "category" in schema
        assert schema["category"] == "TAG"
        assert "price" in schema
        assert schema["price"] == "NUMERIC"

    async def test_get_schema_returns_empty_for_unknown(
        self, async_client: async_redis.Redis
    ):
        """get_schema() returns empty dict for unknown index."""
        registry = AsyncSchemaRegistry(async_client)
        await registry.load_all()

        schema = registry.get_schema("nonexistent_index")
        assert schema == {}


class TestAsyncExecutorBasic:
    """Tests for basic async query execution."""

    async def test_select_all(self, async_executor: AsyncExecutor, products_index: str):
        """SELECT * returns all documents."""
        result = await async_executor.execute(f"SELECT * FROM {products_index}")
        assert result.count == 4
        assert len(result.rows) == 4

    async def test_result_is_query_result(
        self, async_executor: AsyncExecutor, products_index: str
    ):
        """Result should be a QueryResult instance."""
        result = await async_executor.execute(f"SELECT * FROM {products_index}")
        assert isinstance(result, QueryResult)
        assert hasattr(result, "rows")
        assert hasattr(result, "count")

    async def test_select_with_tag_filter(
        self, async_executor: AsyncExecutor, products_index: str
    ):
        """SELECT with tag filter."""
        result = await async_executor.execute(
            f"SELECT * FROM {products_index} WHERE category = 'books'"
        )
        assert result.count == 2
        for row in result.rows:
            assert row["category"] == "books"

    async def test_select_with_numeric_filter(
        self, async_executor: AsyncExecutor, products_index: str
    ):
        """SELECT with numeric comparison."""
        result = await async_executor.execute(
            f"SELECT * FROM {products_index} WHERE price < 50"
        )
        assert result.count >= 2
        for row in result.rows:
            assert float(row["price"]) < 50

    async def test_select_with_limit(
        self, async_executor: AsyncExecutor, products_index: str
    ):
        """SELECT with LIMIT."""
        result = await async_executor.execute(f"SELECT * FROM {products_index} LIMIT 2")
        assert len(result.rows) == 2

    async def test_select_with_order_by(
        self, async_executor: AsyncExecutor, products_index: str
    ):
        """SELECT with ORDER BY."""
        result = await async_executor.execute(
            f"SELECT * FROM {products_index} ORDER BY price DESC"
        )
        prices = [float(row["price"]) for row in result.rows]
        assert prices == sorted(prices, reverse=True)


class TestAsyncExecutorAggregation:
    """Tests for async aggregate query execution."""

    async def test_count_all(self, async_executor: AsyncExecutor, products_index: str):
        """SELECT COUNT(*) returns count."""
        result = await async_executor.execute(f"SELECT COUNT(*) FROM {products_index}")
        assert len(result.rows) == 1
        row = result.rows[0]
        count_value = row.get("COUNT(*)", row.get("count", None))
        assert count_value is not None

    async def test_group_by_with_count(
        self, async_executor: AsyncExecutor, products_index: str
    ):
        """SELECT with GROUP BY and COUNT."""
        result = await async_executor.execute(
            f"SELECT category, COUNT(*) as cnt FROM {products_index} GROUP BY category"
        )
        assert len(result.rows) == 2  # electronics and books
        categories = {row["category"] for row in result.rows}
        assert categories == {"electronics", "books"}


class TestAsyncExecutorParams:
    """Tests for parameterized async execution."""

    async def test_numeric_param(
        self, async_executor: AsyncExecutor, products_index: str
    ):
        """Execute with numeric parameter."""
        result = await async_executor.execute(
            f"SELECT * FROM {products_index} WHERE price > :min_price",
            params={"min_price": 40},
        )
        for row in result.rows:
            assert float(row["price"]) > 40

    async def test_string_param(
        self, async_executor: AsyncExecutor, products_index: str
    ):
        """Execute with string parameter."""
        result = await async_executor.execute(
            f"SELECT * FROM {products_index} WHERE category = :cat",
            params={"cat": "books"},
        )
        assert len(result.rows) == 2
        for row in result.rows:
            assert row["category"] == "books"


class TestAsyncVectorSearch:
    """Tests for async vector search execution."""

    @pytest.fixture
    async def vector_index(self, async_client: async_redis.Redis) -> str:
        """Create a vector index with test data."""
        index_name = "async_vectors"
        try:
            await async_client.execute_command("FT.DROPINDEX", index_name, "DD")
        except Exception:
            pass

        await async_client.execute_command(
            "FT.CREATE",
            index_name,
            "ON",
            "HASH",
            "PREFIX",
            "1",
            "async_vec:",
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

        # Use a separate non-decode client for binary data
        raw_client = async_redis.Redis(
            host=async_client.connection_pool.connection_kwargs["host"],
            port=async_client.connection_pool.connection_kwargs["port"],
            decode_responses=False,
        )
        await raw_client.hset(
            "async_vec:1",
            mapping={"title": "First", "embedding": to_bytes([0.1, 0.2, 0.3, 0.4])},
        )
        await raw_client.hset(
            "async_vec:2",
            mapping={"title": "Second", "embedding": to_bytes([0.5, 0.6, 0.7, 0.8])},
        )
        await raw_client.hset(
            "async_vec:3",
            mapping={"title": "Third", "embedding": to_bytes([0.9, 0.8, 0.7, 0.6])},
        )
        await raw_client.aclose()

        yield index_name

        # Cleanup
        try:
            await async_client.execute_command("FT.DROPINDEX", index_name, "DD")
        except Exception:
            pass

    async def test_vector_search_with_param(
        self, async_client: async_redis.Redis, vector_index: str
    ):
        """Vector search with vector parameter."""
        registry = AsyncSchemaRegistry(async_client)
        await registry.load_all()
        executor = AsyncExecutor(async_client, registry)

        query_vector = struct.pack("4f", 0.1, 0.2, 0.3, 0.4)
        result = await executor.execute(
            f"SELECT title, vector_distance(embedding, :vec) AS score "
            f"FROM {vector_index} LIMIT 3",
            params={"vec": query_vector},
        )
        assert len(result.rows) <= 3
        # First result should be closest to query vector
        assert result.rows[0]["title"] == "First"
        # Verify vector distance score is returned
        assert "score" in result.rows[0]
        score = float(result.rows[0]["score"])
        assert score >= 0  # Distance should be non-negative
