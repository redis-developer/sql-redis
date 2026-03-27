"""Integration tests for lazy schema loading and invalidation.

Tests run against a real Redis 8 instance to verify end-to-end behavior:
- get_schema() lazily loads via FT.INFO on cache miss (no load_all() needed)
- get_field_type() triggers lazy load via get_schema()
- invalidate() clears cached schemas, forcing reload on next access
- load_all() backward compatibility is preserved
- Executor works with lazy-loaded registry
"""

import pytest
import redis

from sql_redis.executor import Executor
from sql_redis.schema import SchemaRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def caching_indexes(redis_client: redis.Redis) -> list[str]:
    """Create multiple indexes for schema caching tests.

    Creates 3 indexes to verify lazy loading only fetches the requested one:
        - cache_products: title TEXT, price NUMERIC, category TAG
        - cache_users:    name TEXT, email TAG, age NUMERIC
        - cache_orders:   order_id TEXT, total NUMERIC, status TAG
    """
    index_configs = [
        (
            "cache_products",
            "cacheprod:",
            ["title", "TEXT", "SORTABLE", "price", "NUMERIC", "category", "TAG"],
        ),
        (
            "cache_users",
            "cacheuser:",
            ["name", "TEXT", "email", "TAG", "age", "NUMERIC"],
        ),
        (
            "cache_orders",
            "cacheorder:",
            ["order_id", "TEXT", "total", "NUMERIC", "status", "TAG"],
        ),
    ]

    created = []
    for index_name, prefix, fields in index_configs:
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
            prefix,
            "SCHEMA",
            *fields,
        )
        created.append(index_name)

    yield created

    # Cleanup
    for index_name in created:
        try:
            redis_client.execute_command("FT.DROPINDEX", index_name, "DD")
        except redis.ResponseError:
            pass


@pytest.fixture
def caching_data(redis_client: redis.Redis, caching_indexes: list[str]) -> list[str]:
    """Populate the cache_products index with test data.

    Documents:
        cacheprod:1  title=Laptop   price=999  category=electronics
        cacheprod:2  title=Book     price=29   category=books
    """
    redis_client.hset(
        "cacheprod:1",
        mapping={"title": "Laptop", "price": "999", "category": "electronics"},
    )
    redis_client.hset(
        "cacheprod:2",
        mapping={"title": "Book", "price": "29", "category": "books"},
    )
    return caching_indexes


# ---------------------------------------------------------------------------
# Tests: Lazy get_schema()
# ---------------------------------------------------------------------------


class TestLazyGetSchema:
    """get_schema() lazily loads index schema via FT.INFO on cache miss."""

    def test_get_schema_loads_on_first_call(
        self, redis_client: redis.Redis, caching_indexes: list[str]
    ):
        """get_schema() without prior load_all() fetches schema via FT.INFO."""
        registry = SchemaRegistry(redis_client)
        # No load_all() — go directly to get_schema()
        schema = registry.get_schema("cache_products")

        assert schema == {"title": "TEXT", "price": "NUMERIC", "category": "TAG"}

    def test_get_schema_caches_after_first_call(
        self, redis_client: redis.Redis, caching_indexes: list[str]
    ):
        """Second get_schema() for the same index returns cached result."""
        registry = SchemaRegistry(redis_client)
        schema1 = registry.get_schema("cache_products")
        schema2 = registry.get_schema("cache_products")

        assert schema1 == schema2
        # Verify it's the same dict object (cached, not re-fetched)
        assert schema1 is schema2

    def test_get_schema_nonexistent_index(
        self, redis_client: redis.Redis, caching_indexes: list[str]
    ):
        """get_schema() for a non-existent index returns empty dict."""
        registry = SchemaRegistry(redis_client)
        schema = registry.get_schema("nonexistent_index_xyz")

        assert schema == {}

    def test_get_schema_only_loads_requested_index(
        self, redis_client: redis.Redis, caching_indexes: list[str]
    ):
        """get_schema() only loads the requested index, not all indexes."""
        registry = SchemaRegistry(redis_client)
        registry.get_schema("cache_products")

        # Only cache_products should be cached, not cache_users or cache_orders
        assert "cache_products" in registry._schemas
        assert "cache_users" not in registry._schemas
        assert "cache_orders" not in registry._schemas


# ---------------------------------------------------------------------------
# Tests: Lazy get_field_type()
# ---------------------------------------------------------------------------


class TestLazyGetFieldType:
    """get_field_type() triggers lazy load via get_schema()."""

    def test_get_field_type_returns_correct_types(
        self, redis_client: redis.Redis, caching_indexes: list[str]
    ):
        """get_field_type() returns correct type for each field after lazy load."""
        registry = SchemaRegistry(redis_client)

        assert registry.get_field_type("cache_products", "title") == "TEXT"
        assert registry.get_field_type("cache_products", "price") == "NUMERIC"
        assert registry.get_field_type("cache_products", "category") == "TAG"

    def test_get_field_type_returns_none_for_unknown_field(
        self, redis_client: redis.Redis, caching_indexes: list[str]
    ):
        """get_field_type() returns None for a field that doesn't exist."""
        registry = SchemaRegistry(redis_client)
        field_type = registry.get_field_type("cache_products", "nonexistent_field")

        assert field_type is None


# ---------------------------------------------------------------------------
# Tests: invalidate()
# ---------------------------------------------------------------------------


class TestInvalidate:
    """invalidate() clears cached schemas, forcing reload on next access."""

    def test_invalidate_specific_index(
        self, redis_client: redis.Redis, caching_indexes: list[str]
    ):
        """invalidate(index) removes only that index from the cache."""
        registry = SchemaRegistry(redis_client)
        # Load two indexes
        registry.get_schema("cache_products")
        registry.get_schema("cache_users")

        assert "cache_products" in registry._schemas
        assert "cache_users" in registry._schemas

        # Invalidate only products
        registry.invalidate("cache_products")

        assert "cache_products" not in registry._schemas
        assert "cache_users" in registry._schemas

    def test_invalidate_all(
        self, redis_client: redis.Redis, caching_indexes: list[str]
    ):
        """invalidate() with no args clears all cached schemas."""
        registry = SchemaRegistry(redis_client)
        registry.get_schema("cache_products")
        registry.get_schema("cache_users")

        registry.invalidate()

        assert len(registry._schemas) == 0

    def test_invalidate_then_get_schema_reloads(
        self, redis_client: redis.Redis, caching_indexes: list[str]
    ):
        """After invalidate(), next get_schema() re-fetches via FT.INFO."""
        registry = SchemaRegistry(redis_client)
        schema_before = registry.get_schema("cache_products")

        registry.invalidate("cache_products")
        schema_after = registry.get_schema("cache_products")

        # Same data but different dict objects (re-fetched)
        assert schema_after == schema_before
        assert schema_after is not schema_before

    def test_invalidate_nonexistent_is_noop(
        self, redis_client: redis.Redis, caching_indexes: list[str]
    ):
        """invalidate() on an index that isn't cached doesn't raise."""
        registry = SchemaRegistry(redis_client)
        # Should not raise
        registry.invalidate("totally_not_cached")


# ---------------------------------------------------------------------------
# Tests: load_all() backward compatibility
# ---------------------------------------------------------------------------


class TestLoadAllBackwardCompat:
    """load_all() still works and coexists with lazy loading."""

    def test_load_all_still_works(
        self, redis_client: redis.Redis, caching_indexes: list[str]
    ):
        """load_all() loads all indexes, get_schema() returns from cache."""
        registry = SchemaRegistry(redis_client)
        registry.load_all()

        # All three indexes should be loaded
        for index_name in caching_indexes:
            schema = registry.get_schema(index_name)
            assert len(schema) > 0, f"Schema for {index_name} should have fields"

    def test_load_all_then_invalidate_then_lazy(
        self, redis_client: redis.Redis, caching_indexes: list[str]
    ):
        """load_all() → invalidate() → get_schema() re-fetches lazily."""
        registry = SchemaRegistry(redis_client)
        registry.load_all()

        registry.invalidate()
        assert len(registry._schemas) == 0

        # Lazy load just one
        schema = registry.get_schema("cache_products")
        assert schema == {"title": "TEXT", "price": "NUMERIC", "category": "TAG"}
        # Only products should be in cache, not the others
        assert "cache_users" not in registry._schemas


# ---------------------------------------------------------------------------
# Tests: Executor with lazy-loaded registry
# ---------------------------------------------------------------------------


class TestLazySchemaWithExecutor:
    """Executor works correctly with lazy-loaded registry (no load_all())."""

    def test_executor_works_without_load_all(
        self, redis_client: redis.Redis, caching_data: list[str]
    ):
        """Executor can execute queries with lazy-loaded registry."""
        registry = SchemaRegistry(redis_client)
        # No load_all() — registry will lazy-load when executor needs it
        executor = Executor(redis_client, registry)

        result = executor.execute(
            "SELECT * FROM cache_products WHERE category = 'electronics'"
        )
        assert result.count == 1
        assert result.rows[0]["title"] == "Laptop"

    def test_executor_multiple_queries_reuse_cache(
        self, redis_client: redis.Redis, caching_data: list[str]
    ):
        """Multiple queries reuse the same cached schema."""
        registry = SchemaRegistry(redis_client)
        executor = Executor(redis_client, registry)

        result1 = executor.execute(
            "SELECT * FROM cache_products WHERE category = 'electronics'"
        )
        schema_after_first = registry._schemas.get("cache_products")

        result2 = executor.execute(
            "SELECT * FROM cache_products WHERE category = 'books'"
        )
        schema_after_second = registry._schemas.get("cache_products")

        assert result1.count == 1
        assert result2.count == 1
        # Same dict object — was not re-fetched
        assert schema_after_first is schema_after_second
