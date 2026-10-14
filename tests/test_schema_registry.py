"""Tests for the SchemaRegistry class."""

import time

import pytest
import redis

from sql_redis.schema import SchemaRegistry, _parse_schema_from_info

pytestmark = pytest.mark.protocol


def _create_test_indexes(redis_client: redis.Redis) -> list[str]:
    """Helper to create test indexes."""
    # Clean up any existing indexes
    for index_name in redis_client.execute_command("FT._LIST"):
        redis_client.execute_command("FT.DROPINDEX", index_name, "DD")

    # Create products index
    redis_client.execute_command(
        "FT.CREATE",
        "products",
        "ON",
        "HASH",
        "PREFIX",
        "1",
        "product:",
        "SCHEMA",
        "title",
        "TEXT",
        "SORTABLE",
        "price",
        "NUMERIC",
        "SORTABLE",
        "category",
        "TAG",
    )

    # Create users index
    redis_client.execute_command(
        "FT.CREATE",
        "users",
        "ON",
        "HASH",
        "PREFIX",
        "1",
        "user:",
        "SCHEMA",
        "name",
        "TEXT",
        "email",
        "TAG",
        "age",
        "NUMERIC",
    )

    # Create stores index with GEO field
    redis_client.execute_command(
        "FT.CREATE",
        "stores",
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

    # Create vectors index with VECTOR field
    redis_client.execute_command(
        "FT.CREATE",
        "vectors",
        "ON",
        "HASH",
        "PREFIX",
        "1",
        "vec:",
        "SCHEMA",
        "id",
        "TEXT",
        "embedding",
        "VECTOR",
        "FLAT",
        "6",
        "TYPE",
        "FLOAT32",
        "DIM",
        "128",
        "DISTANCE_METRIC",
        "COSINE",
    )

    return ["products", "users", "stores", "vectors"]


@pytest.fixture(scope="module")
def multi_index_setup(redis_client: redis.Redis):
    """Create multiple indexes for schema registry testing (module-scoped)."""
    return _create_test_indexes(redis_client)


@pytest.fixture
def multi_index_setup_fresh(redis_client: redis.Redis):
    """Create multiple indexes for schema registry testing (function-scoped).

    Use this fixture for tests that modify or delete indexes.
    """
    return _create_test_indexes(redis_client)


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


class TestSchemaRegistryParsing:
    """Tests for schema parsing edge cases."""

    def test_parse_schema_no_attributes_section(self):
        """_parse_schema_from_info handles response without attributes."""
        # FT.INFO response without 'attributes' key
        fake_info = ["index_name", "test", "other_key", "value"]
        schema = _parse_schema_from_info(fake_info)

        assert schema == {}

    def test_parse_schema_incomplete_attribute(self):
        """_parse_schema_from_info handles attribute without type."""
        # FT.INFO response with attribute but missing type
        fake_info = [
            "attributes",
            [
                ["identifier", "field1", "attribute", "field1"],  # No type
                ["identifier", "field2", "attribute", "field2", "type", "TEXT"],
            ],
        ]
        schema = _parse_schema_from_info(fake_info)

        # Only field2 should be captured (field1 has no type)
        assert schema == {"field2": "TEXT"}

    def test_parse_schema_dict_reply_with_bytes_keys(self):
        """_parse_schema_from_info handles the redis-py 8.x / RESP3 map reply.

        redis-py 8.x applies a response callback to FT.INFO, returning a dict
        with bytes keys whose ``attributes`` value is a list of dicts.
        """
        fake_info = {
            b"index_name": b"items",
            b"attributes": [
                {b"identifier": b"title", b"attribute": b"title", b"type": b"TEXT"},
                {b"identifier": b"genre", b"attribute": b"genre", b"type": b"TAG"},
                {
                    b"identifier": b"embedding",
                    b"attribute": b"embedding",
                    b"type": b"VECTOR",
                },
            ],
        }
        schema = _parse_schema_from_info(fake_info)

        assert schema == {"title": "TEXT", "genre": "TAG", "embedding": "VECTOR"}

    def test_parse_schema_dict_reply_without_attributes(self):
        """A dict reply with no attributes section yields an empty schema."""
        assert _parse_schema_from_info({b"index_name": b"items"}) == {}

    def test_parse_schema_dict_attribute_missing_type(self):
        """A dict attribute without a type is skipped."""
        fake_info = {
            b"attributes": [
                {b"attribute": b"field1"},  # no type
                {b"attribute": b"field2", b"type": b"TEXT"},
            ],
        }
        assert _parse_schema_from_info(fake_info) == {"field2": "TEXT"}


class TestSchemaRegistryRefresh:
    """Tests for schema refresh functionality."""

    def test_refresh_updates_single_index(
        self, redis_client: redis.Redis, multi_index_setup_fresh: list[str]
    ):
        """refresh() should update schema for a single index."""
        registry = SchemaRegistry(redis_client)
        registry.load_all()

        # Verify initial state
        assert "title" in registry.get_schema("products")

        # Drop and recreate with different schema
        redis_client.execute_command("FT.DROPINDEX", "products", "DD")
        redis_client.execute_command(
            "FT.CREATE",
            "products",
            "ON",
            "HASH",
            "PREFIX",
            "1",
            "product:",
            "SCHEMA",
            "name",
            "TEXT",  # Changed from title
            "cost",
            "NUMERIC",  # Changed from price
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
        self, redis_client: redis.Redis, multi_index_setup_fresh: list[str]
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
        self, redis_client: redis.Redis, multi_index_setup_fresh: list[str]
    ):
        """refresh() should handle a newly created index."""
        registry = SchemaRegistry(redis_client)
        registry.load_all()

        # Create a new index
        redis_client.execute_command(
            "FT.CREATE",
            "new_index",
            "ON",
            "HASH",
            "PREFIX",
            "1",
            "new:",
            "SCHEMA",
            "field1",
            "TEXT",
        )

        # Refresh the new index
        registry.refresh("new_index")

        assert registry.get_schema("new_index") == {"field1": "TEXT"}


class TestSchemaRegistryProcessEvents:
    """Tests for process_pending_events()."""

    def test_process_pending_events_when_not_watching(
        self, redis_client: redis.Redis, multi_index_setup: list[str]
    ):
        """process_pending_events() should do nothing when not watching."""
        registry = SchemaRegistry(redis_client)
        registry.load_all()

        # Should return early without error
        registry.process_pending_events()

    def test_process_pending_events_no_changes(
        self, redis_client: redis.Redis, multi_index_setup_fresh: list[str]
    ):
        """process_pending_events() with no index changes."""
        registry = SchemaRegistry(redis_client)
        registry.load_all()

        callbacks_received = []

        def on_schema_change(event_type: str, index_name: str):
            callbacks_received.append((event_type, index_name))

        registry.start_watching(on_change=on_schema_change)

        try:
            # Process events with no changes
            registry.process_pending_events()

            # Should have no callbacks
            assert len(callbacks_received) == 0
        finally:
            registry.stop_watching()

    def test_process_pending_events_multiple_new_indexes(
        self, redis_client: redis.Redis
    ):
        """process_pending_events() detecting multiple new indexes at once."""
        # Start with clean state
        for index_name in redis_client.execute_command("FT._LIST"):
            redis_client.execute_command("FT.DROPINDEX", index_name, "DD")

        registry = SchemaRegistry(redis_client)
        registry.load_all()  # Empty

        callbacks_received = []

        def on_schema_change(event_type: str, index_name: str):
            callbacks_received.append((event_type, index_name))

        registry.start_watching(on_change=on_schema_change)

        try:
            # Create multiple indexes before processing
            redis_client.execute_command(
                "FT.CREATE",
                "idx1",
                "ON",
                "HASH",
                "PREFIX",
                "1",
                "idx1:",
                "SCHEMA",
                "f1",
                "TEXT",
            )
            redis_client.execute_command(
                "FT.CREATE",
                "idx2",
                "ON",
                "HASH",
                "PREFIX",
                "1",
                "idx2:",
                "SCHEMA",
                "f2",
                "TEXT",
            )

            # Process should detect both
            registry.process_pending_events()

            assert len(callbacks_received) == 2
            created_indexes = {idx for _, idx in callbacks_received}
            assert created_indexes == {"idx1", "idx2"}
        finally:
            registry.stop_watching()

    def test_process_pending_events_without_callback(self, redis_client: redis.Redis):
        """process_pending_events() without on_change callback."""
        # Start with clean state
        for index_name in redis_client.execute_command("FT._LIST"):
            redis_client.execute_command("FT.DROPINDEX", index_name, "DD")

        registry = SchemaRegistry(redis_client)
        registry.load_all()  # Empty

        # Start watching without callback
        registry.start_watching()

        try:
            # Create an index
            redis_client.execute_command(
                "FT.CREATE",
                "nocb",
                "ON",
                "HASH",
                "PREFIX",
                "1",
                "nocb:",
                "SCHEMA",
                "f1",
                "TEXT",
            )

            # Process should detect and load without error
            registry.process_pending_events()

            # Schema should be loaded
            assert registry.get_schema("nocb") == {"f1": "TEXT"}

            # Now delete it
            redis_client.execute_command("FT.DROPINDEX", "nocb", "DD")
            registry.process_pending_events()

            # Schema should be removed
            assert registry.get_schema("nocb") == {}
        finally:
            registry.stop_watching()

    def test_process_pending_events_multiple_deleted_indexes(
        self, redis_client: redis.Redis
    ):
        """process_pending_events() detecting multiple deleted indexes at once."""
        # Start with clean state
        for index_name in redis_client.execute_command("FT._LIST"):
            redis_client.execute_command("FT.DROPINDEX", index_name, "DD")

        # Create indexes to delete
        redis_client.execute_command(
            "FT.CREATE",
            "del1",
            "ON",
            "HASH",
            "PREFIX",
            "1",
            "del1:",
            "SCHEMA",
            "f1",
            "TEXT",
        )
        redis_client.execute_command(
            "FT.CREATE",
            "del2",
            "ON",
            "HASH",
            "PREFIX",
            "1",
            "del2:",
            "SCHEMA",
            "f2",
            "TEXT",
        )

        registry = SchemaRegistry(redis_client)
        registry.load_all()

        callbacks_received = []

        def on_schema_change(event_type: str, index_name: str):
            callbacks_received.append((event_type, index_name))

        registry.start_watching(on_change=on_schema_change)

        try:
            # Delete both indexes
            redis_client.execute_command("FT.DROPINDEX", "del1", "DD")
            redis_client.execute_command("FT.DROPINDEX", "del2", "DD")

            # Process should detect both deletions
            registry.process_pending_events()

            assert len(callbacks_received) == 2
            deleted_indexes = {idx for _, idx in callbacks_received}
            assert deleted_indexes == {"del1", "del2"}
        finally:
            registry.stop_watching()


class TestSchemaRegistryWatching:
    """Tests for keyspace notification watching."""

    def test_start_watching_detects_new_index(
        self, redis_client: redis.Redis, multi_index_setup_fresh: list[str]
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
                "FT.CREATE",
                "watched_index",
                "ON",
                "HASH",
                "PREFIX",
                "1",
                "watched:",
                "SCHEMA",
                "data",
                "TEXT",
            )

            # Give pub/sub time to receive the message
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
        self, redis_client: redis.Redis, multi_index_setup_fresh: list[str]
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

            time.sleep(0.5)

            registry.process_pending_events()

            # Verify callback was invoked for drop
            assert len(callbacks_received) > 0

            # Verify schema was removed
            assert registry.get_schema("users") == {}
        finally:
            registry.stop_watching()

    def test_stop_watching_stops_notifications(
        self, redis_client: redis.Redis, multi_index_setup_fresh: list[str]
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
            "FT.CREATE",
            "unwatched_index",
            "ON",
            "HASH",
            "PREFIX",
            "1",
            "unwatched:",
            "SCHEMA",
            "field",
            "TEXT",
        )

        time.sleep(0.5)

        # Should not have received any callbacks
        assert not any(idx == "unwatched_index" for _, idx in callbacks_received)
