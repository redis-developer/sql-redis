"""Tests for the SchemaRegistry class."""

import pytest
import redis

from sql_redis.schema import SchemaRegistry


@pytest.fixture(scope="module")
def multi_index_setup(redis_client: redis.Redis):
    """Create multiple indexes for schema registry testing."""
    # Clean up any existing indexes
    for index_name in redis_client.execute_command("FT._LIST"):
        redis_client.execute_command("FT.DROPINDEX", index_name, "DD")
    
    # Create products index
    redis_client.execute_command(
        "FT.CREATE", "products",
        "ON", "HASH",
        "PREFIX", "1", "product:",
        "SCHEMA",
        "title", "TEXT", "SORTABLE",
        "price", "NUMERIC", "SORTABLE",
        "category", "TAG",
    )
    
    # Create users index
    redis_client.execute_command(
        "FT.CREATE", "users",
        "ON", "HASH",
        "PREFIX", "1", "user:",
        "SCHEMA",
        "name", "TEXT",
        "email", "TAG",
        "age", "NUMERIC",
    )
    
    # Create stores index with GEO field
    redis_client.execute_command(
        "FT.CREATE", "stores",
        "ON", "HASH",
        "PREFIX", "1", "store:",
        "SCHEMA",
        "name", "TEXT",
        "location", "GEO",
    )
    
    # Create vectors index with VECTOR field
    redis_client.execute_command(
        "FT.CREATE", "vectors",
        "ON", "HASH",
        "PREFIX", "1", "vec:",
        "SCHEMA",
        "id", "TEXT",
        "embedding", "VECTOR", "FLAT", "6",
        "TYPE", "FLOAT32", "DIM", "128", "DISTANCE_METRIC", "COSINE",
    )
    
    yield ["products", "users", "stores", "vectors"]


class TestSchemaRegistryLoadAll:
    """Tests for SchemaRegistry.load_all()."""

    def test_load_all_discovers_all_indexes(
        self, redis_client: redis.Redis, multi_index_setup: list[str]
    ):
        """load_all() should discover and load schemas for all indexes."""
        registry = SchemaRegistry(redis_client)
        registry.load_all()
        
        for index_name in multi_index_setup:
            schema = registry.get_schema(index_name)
            assert schema is not None, f"Schema for {index_name} should be loaded"
            assert len(schema) > 0, f"Schema for {index_name} should have fields"

    def test_load_all_captures_field_types(
        self, redis_client: redis.Redis, multi_index_setup: list[str]
    ):
        """load_all() should capture correct field types."""
        registry = SchemaRegistry(redis_client)
        registry.load_all()
        
        # Verify products schema
        products = registry.get_schema("products")
        assert products["title"] == "TEXT"
        assert products["price"] == "NUMERIC"
        assert products["category"] == "TAG"
        
        # Verify stores schema has GEO
        stores = registry.get_schema("stores")
        assert stores["location"] == "GEO"
        
        # Verify vectors schema has VECTOR
        vectors = registry.get_schema("vectors")
        assert vectors["embedding"] == "VECTOR"


class TestSchemaRegistryGetFieldType:
    """Tests for SchemaRegistry.get_field_type()."""

    def test_get_field_type_returns_correct_type(
        self, redis_client: redis.Redis, multi_index_setup: list[str]
    ):
        """get_field_type() should return the correct type for a field."""
        registry = SchemaRegistry(redis_client)
        registry.load_all()
        
        assert registry.get_field_type("products", "title") == "TEXT"
        assert registry.get_field_type("products", "price") == "NUMERIC"
        assert registry.get_field_type("products", "category") == "TAG"
        assert registry.get_field_type("users", "email") == "TAG"
        assert registry.get_field_type("stores", "location") == "GEO"
        assert registry.get_field_type("vectors", "embedding") == "VECTOR"

    def test_get_field_type_returns_none_for_unknown_field(
        self, redis_client: redis.Redis, multi_index_setup: list[str]
    ):
        """get_field_type() should return None for unknown fields."""
        registry = SchemaRegistry(redis_client)
        registry.load_all()
        
        assert registry.get_field_type("products", "nonexistent") is None

    def test_get_field_type_returns_none_for_unknown_index(
        self, redis_client: redis.Redis, multi_index_setup: list[str]
    ):
        """get_field_type() should return None for unknown indexes."""
        registry = SchemaRegistry(redis_client)
        registry.load_all()
        
        assert registry.get_field_type("nonexistent_index", "field") is None


class TestSchemaRegistryGetSchema:
    """Tests for SchemaRegistry.get_schema()."""

    def test_get_schema_returns_full_schema(
        self, redis_client: redis.Redis, multi_index_setup: list[str]
    ):
        """get_schema() should return all fields for an index."""
        registry = SchemaRegistry(redis_client)
        registry.load_all()
        
        products = registry.get_schema("products")
        assert set(products.keys()) == {"title", "price", "category"}

    def test_get_schema_returns_empty_dict_for_unknown_index(
        self, redis_client: redis.Redis, multi_index_setup: list[str]
    ):
        """get_schema() should return empty dict for unknown indexes."""
        registry = SchemaRegistry(redis_client)
        registry.load_all()
        
        assert registry.get_schema("nonexistent") == {}


class TestSchemaRegistryEmptyServer:
    """Tests for SchemaRegistry with no indexes."""

    def test_load_all_handles_no_indexes(self, redis_client: redis.Redis):
        """load_all() should handle a server with no indexes."""
        # Drop all indexes first
        for index_name in redis_client.execute_command("FT._LIST"):
            redis_client.execute_command("FT.DROPINDEX", index_name, "DD")

        registry = SchemaRegistry(redis_client)
        registry.load_all()  # Should not raise

        assert registry.get_schema("anything") == {}


class TestSchemaRegistryRefresh:
    """Tests for schema refresh functionality."""

    def test_refresh_updates_single_index(
        self, redis_client: redis.Redis, multi_index_setup: list[str]
    ):
        """refresh() should update schema for a single index."""
        registry = SchemaRegistry(redis_client)
        registry.load_all()

        # Verify initial state
        assert "title" in registry.get_schema("products")

        # Drop and recreate with different schema
        redis_client.execute_command("FT.DROPINDEX", "products", "DD")
        redis_client.execute_command(
            "FT.CREATE", "products",
            "ON", "HASH",
            "PREFIX", "1", "product:",
            "SCHEMA",
            "name", "TEXT",  # Changed from title
            "cost", "NUMERIC",  # Changed from price
        )

        # Refresh just products
        registry.refresh("products")

        # Verify updated schema
        products = registry.get_schema("products")
        assert "name" in products
        assert "cost" in products
        assert "title" not in products
        assert "price" not in products

    def test_refresh_handles_deleted_index(
        self, redis_client: redis.Redis, multi_index_setup: list[str]
    ):
        """refresh() should handle when an index no longer exists."""
        registry = SchemaRegistry(redis_client)
        registry.load_all()

        # Drop the index
        redis_client.execute_command("FT.DROPINDEX", "products", "DD")

        # Refresh should not raise, should remove from registry
        registry.refresh("products")

        assert registry.get_schema("products") == {}

    def test_refresh_handles_new_index(
        self, redis_client: redis.Redis, multi_index_setup: list[str]
    ):
        """refresh() should handle a newly created index."""
        registry = SchemaRegistry(redis_client)
        registry.load_all()

        # Create a new index
        redis_client.execute_command(
            "FT.CREATE", "new_index",
            "ON", "HASH",
            "PREFIX", "1", "new:",
            "SCHEMA",
            "field1", "TEXT",
        )

        # Refresh the new index
        registry.refresh("new_index")

        assert registry.get_schema("new_index") == {"field1": "TEXT"}


class TestSchemaRegistryWatching:
    """Tests for keyspace notification watching."""

    def test_start_watching_detects_new_index(
        self, redis_client: redis.Redis, multi_index_setup: list[str]
    ):
        """start_watching() should detect when new indexes are created."""
        registry = SchemaRegistry(redis_client)
        registry.load_all()

        # Track callback invocations
        callbacks_received = []

        def on_schema_change(event_type: str, index_name: str):
            callbacks_received.append((event_type, index_name))

        registry.start_watching(on_change=on_schema_change)

        try:
            # Create a new index
            redis_client.execute_command(
                "FT.CREATE", "watched_index",
                "ON", "HASH",
                "PREFIX", "1", "watched:",
                "SCHEMA",
                "data", "TEXT",
            )

            # Give pub/sub time to receive the message
            import time
            time.sleep(0.5)

            # Process pending messages
            registry.process_pending_events()

            # Verify callback was invoked
            assert len(callbacks_received) > 0
            assert any(idx == "watched_index" for _, idx in callbacks_received)

            # Verify schema was updated
            assert registry.get_schema("watched_index") == {"data": "TEXT"}
        finally:
            registry.stop_watching()

    def test_start_watching_detects_dropped_index(
        self, redis_client: redis.Redis, multi_index_setup: list[str]
    ):
        """start_watching() should detect when indexes are dropped."""
        registry = SchemaRegistry(redis_client)
        registry.load_all()

        callbacks_received = []

        def on_schema_change(event_type: str, index_name: str):
            callbacks_received.append((event_type, index_name))

        registry.start_watching(on_change=on_schema_change)

        try:
            # Drop an index
            redis_client.execute_command("FT.DROPINDEX", "users", "DD")

            import time
            time.sleep(0.5)

            registry.process_pending_events()

            # Verify callback was invoked for drop
            assert len(callbacks_received) > 0

            # Verify schema was removed
            assert registry.get_schema("users") == {}
        finally:
            registry.stop_watching()

    def test_stop_watching_stops_notifications(
        self, redis_client: redis.Redis, multi_index_setup: list[str]
    ):
        """stop_watching() should stop receiving notifications."""
        registry = SchemaRegistry(redis_client)
        registry.load_all()

        callbacks_received = []

        def on_schema_change(event_type: str, index_name: str):
            callbacks_received.append((event_type, index_name))

        registry.start_watching(on_change=on_schema_change)
        registry.stop_watching()

        # Create an index after stopping
        redis_client.execute_command(
            "FT.CREATE", "unwatched_index",
            "ON", "HASH",
            "PREFIX", "1", "unwatched:",
            "SCHEMA",
            "field", "TEXT",
        )

        import time
        time.sleep(0.5)

        # Should not have received any callbacks
        assert not any(idx == "unwatched_index" for _, idx in callbacks_received)

