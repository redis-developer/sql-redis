"""SQL Executor - executes translated queries against Redis."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

import redis

from sql_redis.schema import AsyncSchemaRegistry, SchemaRegistry
from sql_redis.translator import Translator

if TYPE_CHECKING:
    import redis.asyncio as async_redis


SchemaCacheStrategy = Literal["lazy", "load_all"]


def _validate_schema_cache_strategy(
    schema_cache_strategy: str,
) -> SchemaCacheStrategy:
    """Validate and normalize the schema cache strategy."""
    if schema_cache_strategy not in {"lazy", "load_all"}:
        raise ValueError("schema_cache_strategy must be one of: 'lazy', 'load_all'")
    return cast(SchemaCacheStrategy, schema_cache_strategy)


def _substitute_params(sql: str, params: dict[str, Any]) -> str:
    """Substitute parameter placeholders in SQL with actual values.

    This is a pure function with no I/O operations, shared by both
    sync and async executors.

    Uses token-based approach: splits SQL on :param patterns, then rebuilds
    with substituted values. This approach solves two critical bugs:

    1. PARTIAL MATCHING BUG: Prevents :id from matching inside :product_id
       by treating each :identifier as a complete token

    2. QUOTE ESCAPING BUG: Properly escapes single quotes in string values
       using SQL standard (single quote -> double single quote)

    Args:
        sql: The SQL string with :param placeholders.
        params: Dictionary mapping parameter names to values.

    Returns:
        SQL string with parameters substituted.

    Implementation Details:
        - Uses regex to split on parameter patterns: :[a-zA-Z_][a-zA-Z0-9_]*
        - Keeps delimiters (the :param tokens) in the split result
        - Iterates through tokens, substituting matched parameters
        - String values are wrapped in single quotes with proper escaping
        - Numeric values are converted to strings
        - Bytes values (e.g., vectors) are NOT substituted here

    Known Limitations:
        - Colons in string literals: SQL like "WHERE x = 'test:value'" would
          theoretically match :value as a parameter. However, this is not a
          practical issue because:
          1. Users pass values via parameters, not hardcoded in SQL
          2. The translator has its own handling of string literals
          3. No real-world use cases have been identified
        - Parameter names are case-sensitive (:id != :ID)
        - Only handles int, float, str types; other types keep placeholder
    """
    if not params:
        return sql

    # Split SQL on :param patterns, keeping the delimiters
    # Pattern matches : followed by valid identifier:
    #   [a-zA-Z_]       - First char must be letter or underscore
    #   [a-zA-Z0-9_]*   - Subsequent chars can be alphanumeric or underscore
    # This prevents partial matching: :id and :product_id are separate tokens
    tokens = re.split(r"(:[a-zA-Z_][a-zA-Z0-9_]*)", sql)

    result = []
    for token in tokens:
        if token.startswith(":"):
            # This is a parameter placeholder
            key = token[1:]  # Remove leading :
            if key in params:
                value = params[key]
                if isinstance(value, (int, float)):
                    # Numeric values: convert to string
                    result.append(str(value))
                elif isinstance(value, str):
                    # String values: wrap in quotes and escape single quotes
                    # SQL standard: ' -> '' (double single quote)
                    # This fixes the quote escaping bug
                    escaped = value.replace("'", "''")
                    result.append(f"'{escaped}'")
                else:
                    # Other types (bytes, None, bool, list, etc.):
                    # Keep placeholder as-is (handled elsewhere or unsupported)
                    result.append(token)
            else:
                # Parameter not provided: keep placeholder as-is
                result.append(token)
        else:
            # Not a parameter: keep as-is
            result.append(token)

    return "".join(result)


@dataclass
class QueryResult:
    """Result of executing a SQL query."""

    rows: list[dict]
    count: int


class _ScoreParseMixin:
    """Shared helpers for score-related response parsing."""

    @staticmethod
    def _is_unknown_command(error_msg: str) -> bool:
        """Return True when a ResponseError means the command is unsupported."""
        lowered = error_msg.lower()
        return "unknown command" in lowered or "unknown subcommand" in lowered

    @staticmethod
    def _parse_hybrid_reply(raw_result) -> tuple[Any, list[dict]]:
        """Parse an FT.HYBRID reply into (count, rows).

        FT.HYBRID does not use the FT.AGGREGATE array shape. The reply is a map
        ``{total_results: N, results: [...], warnings: [...], ...}`` that arrives
        either as a dict (redis-py 8.x / RESP3) or as a flat list
        (``[total_results, N, results, [...], ...]``) on RESP2. Each result row
        is likewise a dict or a flat ``[field, val, ...]`` list. Keys/values may
        be bytes or str depending on the client's decode_responses setting.
        """
        if isinstance(raw_result, dict):
            reply = raw_result
        else:
            reply = dict(zip(raw_result[::2], raw_result[1::2]))

        def _field(name: str):
            if name in reply:
                return reply[name]
            return reply.get(name.encode())

        count = _field("total_results") or 0
        results = _field("results") or []
        rows = [
            dict(row) if isinstance(row, dict) else dict(zip(row[::2], row[1::2]))
            for row in results
        ]
        return count, rows

    @staticmethod
    def _inject_vector_param(cmd: list[str | bytes], vector_param: bytes) -> None:
        """Replace the vector PARAMS value with the actual bytes, in place.

        Only the ``$vector`` token in the PARAMS value position (the one
        preceded by the param name ``vector``) is replaced. Query-side
        references to ``$vector`` (FT.SEARCH KNN expressions, FT.HYBRID VSIM)
        must stay as parameter references so Redis resolves them from PARAMS.
        """
        for i, arg in enumerate(cmd):
            if arg == "$vector" and i > 0 and cmd[i - 1] == "vector":
                cmd[i] = vector_param

    @staticmethod
    def _has_return_0(args: list[str]) -> bool:
        """Return True when the args contain 'RETURN 0' (no document fields)."""
        try:
            idx = args.index("RETURN")
            return args[idx + 1] == "0"
        except (ValueError, IndexError):
            return False

    @staticmethod
    def _resolve_score_alias(
        score_alias: str | None,
        args: list[str],
        first_row_fields: set[str] | None = None,
    ) -> str:
        """Determine a stable score column name that won't collide with
        document fields.  The alias is resolved once and reused for every
        row so all rows share the same column name.

        When a RETURN clause is present, the returned field names are used
        for collision detection.  When RETURN is absent (SELECT *), the
        caller should pass ``first_row_fields`` — the union of all field
        names across all result rows — so we can detect collisions even
        when different documents have different field sets."""
        alias = score_alias or "__score"
        # Extract RETURN field names from args to detect collision
        try:
            idx = args.index("RETURN")
            count = int(args[idx + 1])
            return_fields = set(args[idx + 2 : idx + 2 + count])
        except (ValueError, IndexError):
            # Normalize bytes keys to str so collision detection works
            # regardless of decode_responses setting.
            raw = first_row_fields or set()
            return_fields = {k.decode() if isinstance(k, bytes) else k for k in raw}
        while alias in return_fields:
            alias = f"__score_{alias}"
        return alias


class Executor(_ScoreParseMixin):
    """Executes SQL queries against Redis."""

    def __init__(self, client: redis.Redis, schema_registry: SchemaRegistry) -> None:
        """Initialize executor with Redis client and schema registry."""
        self._client = client
        self._schema_registry = schema_registry
        self._translator = Translator(schema_registry)

    def execute(self, sql: str, *, params: dict | None = None) -> QueryResult:
        """Execute a SQL query and return results."""
        params = params or {}

        # Substitute non-bytes params in SQL using token-based approach
        sql = _substitute_params(sql, params)

        # Translate SQL to Redis command
        translated = self._translator.translate(sql)

        # Build command list and substitute vector params
        # Use list[str | bytes] to allow bytes for vector params
        cmd: list[str | bytes] = list(translated.to_command_list())

        # Find any bytes params (vectors) to substitute
        vector_param: bytes | None = None
        for value in params.values():
            if isinstance(value, bytes):
                vector_param = value
                break

        # Replace the $vector PARAMS value with actual bytes (query/VSIM
        # references to $vector stay as parameter references).
        if vector_param:
            self._inject_vector_param(cmd, vector_param)

        # Execute command
        try:
            raw_result = self._client.execute_command(*cmd)
        except redis.ResponseError as e:
            error_msg = str(e)
            if translated.command == "FT.HYBRID" and self._is_unknown_command(
                error_msg
            ):
                raise redis.ResponseError(
                    f"{error_msg}. hybrid_vector_search() translates to FT.HYBRID, "
                    "which requires Redis 8.4+ (RediSearch with hybrid search) "
                    "and redis-py >= 7.1.0."
                ) from e
            _ismissing_signatures = (
                "Unknown function",
                "No such function",
                "Syntax error",
                "INDEXMISSING",
            )
            if "ismissing(@" in translated.query_string and any(
                sig in error_msg for sig in _ismissing_signatures
            ):
                raise redis.ResponseError(
                    f"{error_msg}. This error may be caused by use of the "
                    "ismissing() function. ismissing() requires Redis 7.4+ "
                    "(RediSearch 2.10+) and the field must have INDEXMISSING "
                    "declared in the schema."
                ) from e
            raise

        # Parse result based on command type
        # FT.SEARCH/FT.AGGREGATE replies are arrays with the count first;
        # FT.HYBRID replies are maps (dict) and set count during parsing below.
        count = raw_result[0] if isinstance(raw_result, list) and raw_result else 0
        rows: list[dict] = []

        if translated.command == "FT.HYBRID":
            count, rows = self._parse_hybrid_reply(raw_result)
        elif translated.command == "FT.SEARCH":
            # Use the explicit score_alias signal rather than scanning args
            # for the literal token "WITHSCORES", which could false-positive
            # if a returned field happened to be named "WITHSCORES".
            with_scores = translated.score_alias is not None
            # RETURN 0 suppresses document fields (like NOCONTENT);
            # with WITHSCORES the reply is [count, id, score, id, score, ...]
            no_content = self._has_return_0(translated.args)

            # Pre-resolve score alias; may be deferred for SELECT *
            score_alias: str | None = None

            if with_scores and no_content:
                # WITHSCORES + RETURN 0: [count, id1, score1, id2, score2, ...]
                # Stride of 2: key, score (no field array)
                score_alias = self._resolve_score_alias(
                    translated.score_alias, translated.args
                )
                for i in range(1, len(raw_result) - 1, 2):
                    score = raw_result[i + 1]
                    row = {score_alias: score}
                    rows.append(row)
            elif with_scores:
                # WITHSCORES format: [count, key1, score1, [fields1], key2, score2, [fields2], ...]
                # Stride of 3: key, score, field_list
                # First pass: collect all field names across all rows so the
                # alias avoids collisions with any document field, not just
                # the first row's fields.
                all_field_names: set[str] = set()
                parsed_rows: list[tuple[dict, Any]] = []
                for i in range(1, len(raw_result) - 2, 3):
                    score = raw_result[i + 1]
                    row_data = raw_result[i + 2]
                    row = dict(zip(row_data[::2], row_data[1::2]))
                    all_field_names.update(row.keys())
                    parsed_rows.append((row, score))
                resolved_alias = self._resolve_score_alias(
                    translated.score_alias,
                    translated.args,
                    first_row_fields=all_field_names,
                )
                for row, score in parsed_rows:
                    row[resolved_alias] = score
                    rows.append(row)
            else:
                # Standard format: [count, key1, [fields1], key2, [fields2], ...]
                for i in range(2, len(raw_result), 2):
                    row_data = raw_result[i]
                    row = dict(zip(row_data[::2], row_data[1::2]))
                    rows.append(row)
        else:
            # FT.AGGREGATE format: [count, [fields1], [fields2], ...]
            for row_data in raw_result[1:]:
                row = dict(zip(row_data[::2], row_data[1::2]))
                rows.append(row)

        return QueryResult(rows=rows, count=count)


class AsyncExecutor(_ScoreParseMixin):
    """Async version of Executor for use with redis.asyncio clients."""

    def __init__(
        self,
        client: "async_redis.Redis",
        schema_registry: AsyncSchemaRegistry,
    ) -> None:
        """Initialize async executor with Redis client and schema registry.

        Args:
            client: An async Redis client (redis.asyncio.Redis).
            schema_registry: An AsyncSchemaRegistry instance.
        """
        self._client = client
        self._schema_registry = schema_registry
        self._translator = Translator(schema_registry)

    async def execute(self, sql: str, *, params: dict | None = None) -> QueryResult:
        """Execute a SQL query asynchronously and return results."""
        params = params or {}

        # Substitute non-bytes params in SQL
        sql = _substitute_params(sql, params)

        # Parse once, ensure schema is loaded (async lazy-load), then
        # translate from the pre-parsed result to avoid double-parsing.
        parsed = self._translator.parse(sql)
        if parsed.index:
            await self._schema_registry.ensure_schema(parsed.index)

        # Translate from pre-parsed query (sync - no Redis calls)
        translated = self._translator.translate_parsed(parsed)

        # Build command list and substitute vector params
        cmd: list[str | bytes] = list(translated.to_command_list())

        # Find any bytes params (vectors) to substitute
        vector_param: bytes | None = None
        for value in params.values():
            if isinstance(value, bytes):
                vector_param = value
                break

        # Replace the $vector PARAMS value with actual bytes (query/VSIM
        # references to $vector stay as parameter references).
        if vector_param:
            self._inject_vector_param(cmd, vector_param)

        # Execute command asynchronously
        try:
            raw_result = await self._client.execute_command(*cmd)
        except redis.ResponseError as e:
            error_msg = str(e)
            if translated.command == "FT.HYBRID" and self._is_unknown_command(
                error_msg
            ):
                raise redis.ResponseError(
                    f"{error_msg}. hybrid_vector_search() translates to FT.HYBRID, "
                    "which requires Redis 8.4+ (RediSearch with hybrid search) "
                    "and redis-py >= 7.1.0."
                ) from e
            _ismissing_signatures = (
                "Unknown function",
                "No such function",
                "Syntax error",
                "INDEXMISSING",
            )
            if "ismissing(@" in translated.query_string and any(
                sig in error_msg for sig in _ismissing_signatures
            ):
                raise redis.ResponseError(
                    f"{error_msg}. This error may be caused by use of the "
                    "ismissing() function. ismissing() requires Redis 7.4+ "
                    "(RediSearch 2.10+) and the field must have INDEXMISSING "
                    "declared in the schema."
                ) from e
            raise

        # Parse result based on command type
        # FT.SEARCH/FT.AGGREGATE replies are arrays with the count first;
        # FT.HYBRID replies are maps (dict) and set count during parsing below.
        count = raw_result[0] if isinstance(raw_result, list) and raw_result else 0
        rows: list[dict] = []

        if translated.command == "FT.HYBRID":
            count, rows = self._parse_hybrid_reply(raw_result)
        elif translated.command == "FT.SEARCH":
            with_scores = translated.score_alias is not None
            no_content = self._has_return_0(translated.args)

            score_alias: str | None = None

            if with_scores and no_content:
                # WITHSCORES + RETURN 0: [count, id1, score1, id2, score2, ...]
                score_alias = self._resolve_score_alias(
                    translated.score_alias, translated.args
                )
                for i in range(1, len(raw_result) - 1, 2):
                    score = raw_result[i + 1]
                    row = {score_alias: score}
                    rows.append(row)
            elif with_scores:
                # WITHSCORES format: [count, key1, score1, [fields1], ...]
                # First pass: collect all field names across all rows so the
                # alias avoids collisions with any document field.
                all_field_names: set[str] = set()
                parsed_rows: list[tuple[dict, Any]] = []
                for i in range(1, len(raw_result) - 2, 3):
                    score = raw_result[i + 1]
                    row_data = raw_result[i + 2]
                    row = dict(zip(row_data[::2], row_data[1::2]))
                    all_field_names.update(row.keys())
                    parsed_rows.append((row, score))
                resolved_alias = self._resolve_score_alias(
                    translated.score_alias,
                    translated.args,
                    first_row_fields=all_field_names,
                )
                for row, score in parsed_rows:
                    row[resolved_alias] = score
                    rows.append(row)
            else:
                # Standard format: [count, key1, [fields1], key2, [fields2], ...]
                for i in range(2, len(raw_result), 2):
                    row_data = raw_result[i]
                    row = dict(zip(row_data[::2], row_data[1::2]))
                    rows.append(row)
        else:
            # FT.AGGREGATE format: [count, [fields1], [fields2], ...]
            for row_data in raw_result[1:]:
                row = dict(zip(row_data[::2], row_data[1::2]))
                rows.append(row)

        return QueryResult(rows=rows, count=count)


def create_executor(
    client: redis.Redis,
    *,
    schema_registry: SchemaRegistry | None = None,
    schema_cache_strategy: SchemaCacheStrategy = "lazy",
) -> Executor:
    """Create a sync SQL executor with the requested schema cache strategy.

    Args:
        client: Redis client used by the executor.
        schema_registry: Optional existing registry to reuse.
        schema_cache_strategy: Schema loading strategy. ``"lazy"`` defers
            ``FT.INFO`` calls until a referenced index is needed. ``"load_all"``
            preserves the historical eager behavior by preloading all schemas.
    """
    schema_cache_strategy = _validate_schema_cache_strategy(schema_cache_strategy)

    registry = schema_registry or SchemaRegistry(client)
    if schema_cache_strategy == "load_all":
        registry.load_all()

    return Executor(client, registry)


async def create_async_executor(
    client: "async_redis.Redis",
    *,
    schema_registry: AsyncSchemaRegistry | None = None,
    schema_cache_strategy: SchemaCacheStrategy = "lazy",
) -> AsyncExecutor:
    """Create an async SQL executor with the requested schema cache strategy.

    Args:
        client: Async Redis client used by the executor.
        schema_registry: Optional existing async registry to reuse.
        schema_cache_strategy: Schema loading strategy. ``"lazy"`` defers
            ``FT.INFO`` calls until a referenced index is needed. ``"load_all"``
            preserves the historical eager behavior by preloading all schemas.
    """
    schema_cache_strategy = _validate_schema_cache_strategy(schema_cache_strategy)

    registry = schema_registry or AsyncSchemaRegistry(client)
    if schema_cache_strategy == "load_all":
        await registry.load_all()

    return AsyncExecutor(client, registry)
