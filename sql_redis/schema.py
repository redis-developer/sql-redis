"""Schema registry for Redis search indexes."""

from typing import Callable

import redis


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
            self._load_index_schema(index_name)

    def _load_index_schema(self, index_name: str) -> None:
        """Load schema for a single index."""
        try:
            info = self._client.execute_command("FT.INFO", index_name)
            schema = self._parse_schema_from_info(info)
            self._schemas[index_name] = schema
        except redis.ResponseError:
            # Index doesn't exist or was deleted
            self._schemas.pop(index_name, None)

    def _parse_schema_from_info(self, info: list) -> dict[str, str]:
        """Parse field types from FT.INFO response."""
        schema = {}
        # Find the 'attributes' section in the info response
        for i, item in enumerate(info):
            if item == "attributes":
                attributes = info[i + 1]
                for attr in attributes:
                    field_name = None
                    field_type = None
                    # Each attribute is a list like:
                    # ['identifier', 'title', 'attribute', 'title', 'type', 'TEXT', ...]
                    for j, val in enumerate(attr):
                        if val == "attribute" and j + 1 < len(attr):
                            field_name = attr[j + 1]
                        if val == "type" and j + 1 < len(attr):
                            field_type = attr[j + 1]
                    if field_name and field_type:
                        schema[field_name] = field_type
                break
        return schema

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

