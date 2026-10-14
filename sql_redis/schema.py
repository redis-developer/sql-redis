"""Schema registry for Redis search indexes."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable

import redis

if TYPE_CHECKING:
    import redis.asyncio as async_redis


def _decode(value: Any) -> Any:
    """Decode a bytes value to str; pass through everything else."""
    return value.decode("utf-8") if isinstance(value, bytes) else value


def _extract_attributes(info: Any) -> list:
    """Pull the ``attributes`` section out of an FT.INFO reply.

    Handles both reply shapes: the RESP2 flat list
    (``[..., 'attributes', [...], ...]``) and the RESP3 map
    (``{b'attributes': [...], ...}``), whose keys may be bytes or str.
    """
    if isinstance(info, dict):
        for key, val in info.items():
            if _decode(key) == "attributes":
                return val or []
        return []
    for i, item in enumerate(info):
        if _decode(item) == "attributes":
            return info[i + 1]
    return []


def _attribute_name_and_type(attr: Any) -> tuple[str | None, str | None]:
    """Extract (field_name, field_type) from a single FT.INFO attribute.

    An attribute is either a dict (redis-py 8.x), e.g.
    ``{b'attribute': b'title', b'type': b'TEXT', ...}``, or a flat list, e.g.
    ``[b'identifier', b'title', b'attribute', b'title', b'type', b'TEXT', ...]``.
    """
    if isinstance(attr, dict):
        name = next(
            (_decode(v) for k, v in attr.items() if _decode(k) == "attribute"), None
        )
        ftype = next(
            (_decode(v) for k, v in attr.items() if _decode(k) == "type"), None
        )
        return name, ftype

    name = None
    ftype = None
    for j, val in enumerate(attr):
        val_str = _decode(val)
        if val_str == "attribute" and j + 1 < len(attr):
            name = _decode(attr[j + 1])
        if val_str == "type" and j + 1 < len(attr):
            ftype = _decode(attr[j + 1])
    return name, ftype


def _parse_schema_from_info(info: Any) -> dict[str, str]:
    """Parse field types from an FT.INFO response.

    This is a pure function with no I/O operations, shared by both the sync
    and async schema registries. It accepts both the RESP2 list reply and the
    RESP3 map reply (see ``_extract_attributes``).

    Args:
        info: The raw response from the FT.INFO command (list or dict).

    Returns:
        Dictionary mapping field names to their types (e.g., {"title": "TEXT"}).
    """
    schema: dict[str, str] = {}
    for attr in _extract_attributes(info):
        field_name, field_type = _attribute_name_and_type(attr)
        if field_name and field_type:
            schema[field_name] = field_type
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
        """Load schema for a single index.

        If the index exists, caches its schema. If the index does not exist,
        removes it from the cache (if present) so the next access retries
        FT.INFO — allowing recovery when an index is created after first access.
        """
        try:
            info = self._client.execute_command("FT.INFO", index_name)
            schema = _parse_schema_from_info(info)
            self._schemas[index_name] = schema
        except redis.ResponseError as e:
            msg = str(e).lower()
            if "no such index" in msg or "unknown index" in msg:
                # Index doesn't exist — remove from cache so the next
                # get_schema() call retries FT.INFO (transient miss recovery).
                self._schemas.pop(index_name, None)
            else:
                raise

    def get_field_type(self, index: str, field: str) -> str | None:
        """Get field type for a given index and field.

        Lazily loads the index schema if not already cached.
        Returns None if index doesn't exist or field is unknown.
        """
        schema = self.get_schema(index)
        return schema.get(field)

    def get_schema(self, index: str) -> dict[str, str]:
        """Get full schema for an index, loading lazily if not cached.

        On first access for a given index, issues a single FT.INFO call
        to Redis. Subsequent calls return the cached schema with no I/O.

        Returns empty dict if index does not exist in Redis.
        """
        if index not in self._schemas:
            self._load_index_schema(index)
        return self._schemas.get(index, {})

    def invalidate(self, index: str | None = None) -> None:
        """Invalidate cached schema(s), forcing reload on next access.

        Args:
            index: Specific index to invalidate. If None, invalidates all.
        """
        if index is not None:
            self._schemas.pop(index, None)
        else:
            self._schemas.clear()

    def refresh(self, index_name: str) -> None:
        """Refresh schema for a single index.

        If the index no longer exists, removes it from the registry.
        If the index is new or changed, updates its cached schema.
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

        # Get current indexes from Redis (decode bytes to str for comparison)
        raw_indexes = self._client.execute_command("FT._LIST")
        current_indexes = {
            idx.decode("utf-8") if isinstance(idx, bytes) else idx
            for idx in raw_indexes
        }

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
        self._loading: dict[str, asyncio.Task[None]] = {}

    async def load_all(self) -> None:
        """Load schemas for all indexes on the server.

        Uses asyncio.gather() to load all index schemas concurrently.
        Cancels any in-flight ensure_schema() tasks first.
        """
        self._cancel_all_inflight()
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
        """Load schema for a single index.

        If the index exists, caches its schema. If the index does not exist,
        removes it from the cache (if present) so the next access retries
        FT.INFO — allowing recovery when an index is created after first access.
        """
        try:
            info = await self._client.execute_command("FT.INFO", index_name)
            schema = _parse_schema_from_info(info)
            self._schemas[index_name] = schema
        except redis.ResponseError as e:
            msg = str(e).lower()
            if "no such index" in msg or "unknown index" in msg:
                # Index doesn't exist — remove from cache so the next
                # ensure_schema() call retries FT.INFO (transient miss recovery).
                self._schemas.pop(index_name, None)
            else:
                raise

    def get_field_type(self, index: str, field: str) -> str | None:
        """Get field type for a given index and field.

        Note: For async lazy loading, call ensure_schema() first.
        Returns None if index or field is unknown.
        """
        schema = self._schemas.get(index, {})
        return schema.get(field)

    def get_schema(self, index: str) -> dict[str, str]:
        """Get full schema for an index (sync access to cache).

        Returns empty dict if index is not cached. Use ensure_schema()
        to load lazily in async contexts.
        """
        return self._schemas.get(index, {})

    async def ensure_schema(self, index: str) -> dict[str, str]:
        """Ensure schema for an index is loaded, fetching lazily if needed.

        This is the async equivalent of the sync get_schema() lazy path.
        On first access for a given index, issues a single FT.INFO call.
        Subsequent calls return the cached schema with no I/O.

        Concurrent calls for the same index share a single in-flight
        FT.INFO task instead of issuing duplicate requests.

        If the in-flight task is cancelled (e.g. by invalidate()), the
        current cache state is returned instead of propagating
        CancelledError to callers.

        Returns empty dict if index does not exist in Redis.
        """
        if index in self._schemas:
            return self._schemas[index]

        if index not in self._loading:
            new_task = asyncio.create_task(self._load_index_schema(index))
            self._loading[index] = new_task

            # Attach a done-callback to clean up _loading even if all
            # awaiters are cancelled (no finally block would run).
            def _cleanup_loading(t: asyncio.Task[None], _index: str = index) -> None:
                if self._loading.get(_index) is t:
                    self._loading.pop(_index, None)

            new_task.add_done_callback(_cleanup_loading)

        maybe_task = self._loading.get(index)
        if maybe_task is None:
            # Task was removed (e.g. by invalidate()) before we could await it
            return self._schemas.get(index, {})

        task = maybe_task  # narrowed to non-None

        try:
            # Shield the shared task so that caller cancellation (e.g.
            # asyncio.wait_for timeout) does not cancel the shared FT.INFO
            # for other awaiters. invalidate()/refresh()/load_all() still
            # cancel the underlying task directly via task.cancel().
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if not task.cancelled():
                # The shared load task is still running — this CancelledError
                # came from the *caller* being cancelled (e.g. asyncio.wait_for
                # timeout). Propagate so the caller actually aborts.
                raise
            # invalidate()/refresh()/load_all() cancelled the in-flight load.
            # Return the current (post-invalidate) cache state rather than
            # propagating cancellation to higher-level callers.
            return self._schemas.get(index, {})
        finally:
            # Only remove if this is still the current task for this index
            # and the underlying task has finished. If the caller was
            # cancelled while the shielded task continues running, keep it
            # registered so other callers still share the same in-flight task.
            if self._loading.get(index) is task and task.done():
                self._loading.pop(index, None)

        return self._schemas.get(index, {})

    def invalidate(self, index: str | None = None) -> None:
        """Invalidate cached schema(s), forcing reload on next access.

        Also cancels any in-flight ensure_schema() tasks for the
        invalidated index(es) to prevent stale data from being written.

        Args:
            index: Specific index to invalidate. If None, invalidates all.
        """
        if index is not None:
            self._schemas.pop(index, None)
            task = self._loading.pop(index, None)
            if task is not None:
                task.cancel()
        else:
            self._cancel_all_inflight()
            self._schemas.clear()

    def _cancel_all_inflight(self) -> None:
        """Cancel all in-flight loading tasks."""
        for task in self._loading.values():
            task.cancel()
        self._loading.clear()

    async def refresh(self, index_name: str) -> None:
        """Refresh schema for a single index.

        Cancels any in-flight ensure_schema() task for this index first.
        If the index no longer exists, removes it from the registry.
        """
        task = self._loading.pop(index_name, None)
        if task is not None:
            task.cancel()
        await self._load_index_schema(index_name)
