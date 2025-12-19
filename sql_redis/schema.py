"""Schema registry for Redis search indexes."""

from typing import Callable


class SchemaRegistry:
    """Loads and caches index schemas from Redis.

    Supports automatic schema refresh via Redis keyspace notifications.
    """

    def __init__(self, redis_client):
        raise NotImplementedError("SchemaRegistry is not yet implemented")

    def load_all(self) -> None:
        """Load schemas for all indexes on the server."""
        raise NotImplementedError("load_all is not yet implemented")

    def get_field_type(self, index: str, field: str) -> str | None:
        """Get field type for a given index and field.

        Returns None if index or field is unknown.
        """
        raise NotImplementedError("get_field_type is not yet implemented")

    def get_schema(self, index: str) -> dict[str, str]:
        """Get full schema for an index.

        Returns empty dict if index is unknown.
        """
        raise NotImplementedError("get_schema is not yet implemented")

    def refresh(self, index_name: str) -> None:
        """Refresh schema for a single index.

        If the index no longer exists, removes it from the registry.
        If the index is new, adds it to the registry.
        """
        raise NotImplementedError("refresh is not yet implemented")

    def start_watching(
        self,
        on_change: Callable[[str, str], None] | None = None
    ) -> None:
        """Start watching for index changes via keyspace notifications.

        Args:
            on_change: Optional callback invoked with (event_type, index_name)
                       when an index is created, dropped, or altered.
        """
        raise NotImplementedError("start_watching is not yet implemented")

    def stop_watching(self) -> None:
        """Stop watching for index changes."""
        raise NotImplementedError("stop_watching is not yet implemented")

    def process_pending_events(self) -> None:
        """Process any pending keyspace notification events.

        Call this periodically if not using a background thread.
        """
        raise NotImplementedError("process_pending_events is not yet implemented")

