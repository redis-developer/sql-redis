"""Schema registry for Redis search indexes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import redis

if TYPE_CHECKING:
    import redis.asyncio as async_redis


def _parse_schema_from_info(info: list) -> dict[str, str]:
    """Parse field types from FT.INFO response.

    This is a pure function with no I/O operations, shared by both
    sync and async schema registries.

    Args:
        info: The raw response from FT.INFO command.

    Returns:
        Dictionary mapping field names to their types (e.g., {"title": "TEXT"}).
    """
    schema = {}
    # Find the 'attributes' section in the info response
    for i, item in enumerate(info):
        # Handle bytes or string comparison
        item_str = item.decode("utf-8") if isinstance(item, bytes) else item
        if item_str == "attributes":
            attributes = info[i + 1]
            for attr in attributes:
                field_name = None
                field_type = None
                # Each attribute is a list like:
                # [b'identifier', b'title', b'attribute', b'title', b'type', b'TEXT', ...]
                for j, val in enumerate(attr):
                    val_str = val.decode("utf-8") if isinstance(val, bytes) else val
                    if val_str == "attribute" and j + 1 < len(attr):
                        fn = attr[j + 1]
                        field_name = fn.decode("utf-8") if isinstance(fn, bytes) else fn
                    if val_str == "type" and j + 1 < len(attr):
                        ft = attr[j + 1]
                        field_type = ft.decode("utf-8") if isinstance(ft, bytes) else ft
                if field_name and field_type:
                    schema[field_name] = field_type
            break
    return schema


class SchemaRegistry:
    """Loads and caches index schemas from Redis.

    Supports automatic schema refresh via Redis keyspace notifications.
    """

    def __init__(self, redis_client: redis.Redis):
        self._client = redis_client
        self._schemas: dict[str, dict[str, str]] = {}
        self._on_change: Callable[[str, str], None] | None = None
        self._watching = False

    def load_all(self) -> None:
        """Load schemas for all indexes on the server."""
        self._schemas.clear()
        indexes = self._client.execute_command("FT._LIST")
        for index_name in indexes:
            # Decode bytes to string if needed
            if isinstance(index_name, bytes):
                index_name = index_name.decode("utf-8")
            self._load_index_schema(index_name)

    def _load_index_schema(self, index_name: str) -> None:
        """Load schema for a single index."""
        try:
            info = self._client.execute_command("FT.INFO", index_name)
            schema = _parse_schema_from_info(info)
            self._schemas[index_name] = schema
        except redis.ResponseError:
            # Index doesn't exist or was deleted
            self._schemas.pop(index_name, None)

    def get_field_type(self, index: str, field: str) -> str | None:
        """Get field type for a given index and field.

        Returns None if index or field is unknown.
        """
        schema = self._schemas.get(index, {})
        return schema.get(field)

    def get_schema(self, index: str) -> dict[str, str]:
        """Get full schema for an index.

        Returns empty dict if index is unknown.
        """
        return self._schemas.get(index, {})

    def refresh(self, index_name: str) -> None:
        """Refresh schema for a single index.

        If the index no longer exists, removes it from the registry.
        If the index is new, adds it to the registry.
        """
        self._load_index_schema(index_name)

    def start_watching(
        self, on_change: Callable[[str, str], None] | None = None
    ) -> None:
        """Start watching for index changes.

        Since RediSearch doesn't emit keyspace notifications for FT commands,
        this uses polling via FT._LIST to detect changes.

        Args:
            on_change: Optional callback invoked with (event_type, index_name)
                       when an index is created, dropped, or altered.
        """
        self._on_change = on_change
        self._watching = True

    def stop_watching(self) -> None:
        """Stop watching for index changes."""
        self._watching = False
        self._on_change = None

    def process_pending_events(self) -> None:
        """Process any pending index change events.

        Since RediSearch doesn't emit keyspace notifications, this polls
        FT._LIST to detect new and deleted indexes. Call this periodically.
        """
        if not self._watching:
            return

        # Get current indexes from Redis
        current_indexes = set(self._client.execute_command("FT._LIST"))

        cached_indexes = set(self._schemas.keys())

        # Detect new indexes
        new_indexes = current_indexes - cached_indexes
        for idx in new_indexes:
            self._load_index_schema(idx)
            if self._on_change:
                self._on_change("created", idx)

        # Detect deleted indexes
        deleted_indexes = cached_indexes - current_indexes
        for idx in deleted_indexes:
            self._schemas.pop(idx, None)
            if self._on_change:
                self._on_change("dropped", idx)


class AsyncSchemaRegistry:
    """Async version of SchemaRegistry for use with redis.asyncio clients.

    Loads and caches index schemas from Redis asynchronously.
    """

    def __init__(self, redis_client: "async_redis.Redis") -> None:
        """Initialize with an async Redis client.

        Args:
            redis_client: An async Redis client (redis.asyncio.Redis).
        """
        self._client = redis_client
        self._schemas: dict[str, dict[str, str]] = {}

    async def load_all(self) -> None:
        """Load schemas for all indexes on the server.

        Uses asyncio.gather() to load all index schemas concurrently.
        """
        import asyncio

        self._schemas.clear()
        indexes = await self._client.execute_command("FT._LIST")
        # Decode bytes to strings
        decoded_indexes = [
            idx.decode("utf-8") if isinstance(idx, bytes) else idx for idx in indexes
        ]
        # Load all schemas concurrently
        await asyncio.gather(
            *[self._load_index_schema(name) for name in decoded_indexes]
        )

    async def _load_index_schema(self, index_name: str) -> None:
        """Load schema for a single index."""
        try:
            info = await self._client.execute_command("FT.INFO", index_name)
            schema = _parse_schema_from_info(info)
            self._schemas[index_name] = schema
        except redis.ResponseError:
            # Index doesn't exist or was deleted
            self._schemas.pop(index_name, None)

    def get_field_type(self, index: str, field: str) -> str | None:
        """Get field type for a given index and field.

        Returns None if index or field is unknown.
        """
        schema = self._schemas.get(index, {})
        return schema.get(field)

    def get_schema(self, index: str) -> dict[str, str]:
        """Get full schema for an index.

        Returns empty dict if index is unknown.
        """
        return self._schemas.get(index, {})

    async def refresh(self, index_name: str) -> None:
        """Refresh schema for a single index."""
        await self._load_index_schema(index_name)
