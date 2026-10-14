"""SQL Executor - executes translated queries against Redis."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

import redis

from sql_redis.schema import AsyncSchemaRegistry, SchemaRegistry
from sql_redis.translator import TranslatedQuery, Translator

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


def _map_get(mapping: dict, key: str, default: Any = None) -> Any:
    """Look up a RESP3 map key, tolerating bytes keys.

    RESP3 replies arrive as maps whose *structural* keys (``total_results``,
    ``results``, ``id``, ``score``, ``extra_attributes``) are ``str`` when the
    client decodes responses and ``bytes`` when it does not. Document *field*
    keys never go through this helper: they reach the caller exactly as
    received, so a ``decode_responses=False`` client still gets bytes keys.
    """
    if key in mapping:
        return mapping[key]
    return mapping.get(key.encode(), default)


def _field_array(fields: Any) -> list:
    """Return a document's fields as a flat ``[field, value, ...]`` array.

    Accepts the RESP3 map form and the RESP2 flat form, mirroring
    ``_parse_hybrid_reply``. A nil or unrecognized value becomes an empty
    array, which is the same tolerance the array parser already applies to a
    nil field-array (issue #38).
    """
    if isinstance(fields, dict):
        return [item for pair in fields.items() for item in pair]
    if isinstance(fields, (list, tuple)):
        return list(fields)
    return []


@dataclass
class QueryResult:
    """Result of executing a SQL query."""

    rows: list[dict]
    count: int


def _is_unknown_command(error_msg: str) -> bool:
    """Return True when a ResponseError means the command is unsupported."""
    lowered = error_msg.lower()
    return "unknown command" in lowered or "unknown subcommand" in lowered


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


def _has_return_0(args: list[str]) -> bool:
    """Return True when the args contain 'RETURN 0' (no document fields)."""
    try:
        idx = args.index("RETURN")
        return args[idx + 1] == "0"
    except (ValueError, IndexError):
        return False


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


def _parse_hybrid_reply(raw_result) -> tuple[Any, list[dict]]:
    """Parse an FT.HYBRID reply into (count, rows).

    FT.HYBRID does not use the FT.AGGREGATE array shape. The reply is a map
    ``{total_results: N, results: [...], warnings: [...], ...}`` that arrives
    either as a dict (RESP3) or as a flat list
    (``[total_results, N, results, [...], ...]``) on RESP2. Each result row is
    likewise a dict of *fields* or a flat ``[field, val, ...]`` list, with no
    ``extra_attributes`` nesting: unlike FT.SEARCH, a hybrid row's own keys are
    the field names. Keys and values may be bytes or str depending on the
    client's decode_responses setting.
    """
    if isinstance(raw_result, dict):
        reply = raw_result
    else:
        reply = dict(zip(raw_result[::2], raw_result[1::2]))

    count = _map_get(reply, "total_results") or 0
    results = _map_get(reply, "results") or []
    rows = [
        dict(row) if isinstance(row, dict) else dict(zip(row[::2], row[1::2]))
        for row in results
    ]
    return count, rows


@dataclass(frozen=True)
class _ReplyLayout:
    """How one reply is laid out, derived once per query.

    The array parser strides over the reply, so it and the RESP3 fold have to
    agree on which slots each document occupies. Deriving that here and passing
    it to both is what keeps them from drifting apart.
    """

    is_search: bool
    with_scores: bool
    has_fields: bool
    score_alias: str | None
    args: list[str]

    @classmethod
    def of(cls, translated: TranslatedQuery) -> "_ReplyLayout":
        return cls(
            is_search=translated.command == "FT.SEARCH",
            # The explicit score_alias signal, rather than scanning args for
            # the literal token "WITHSCORES", which could false-positive if a
            # returned field happened to be named "WITHSCORES".
            with_scores=translated.score_alias is not None,
            # RETURN 0 suppresses document fields (like NOCONTENT), so each
            # document arrives without a field array.
            has_fields=not _has_return_0(translated.args),
            score_alias=translated.score_alias,
            args=translated.args,
        )


def _fold_map_to_array(reply: dict, layout: _ReplyLayout) -> list:
    """Fold a RESP3 FT.SEARCH / FT.AGGREGATE map into the RESP2 array shape.

    A RESP3 reply is a map::

        {total_results: N, attributes: [...], format: ..., warning: [...],
         results: [{id: key, score: 0.5, extra_attributes: {field: val},
                    values: [...]}, ...]}

    The output is only meaningful as input to ``_parse_array_reply`` under the
    same ``layout``: it emits the document key, the score when WITHSCORES was
    requested, and the field array unless RETURN 0 suppressed document fields,
    which is exactly the slot layout that parser will stride over. ``id`` lands
    in the position the parser discards; ``values``, ``attributes``, ``format``
    and ``warning`` are not read.

    Values are moved, not converted, so a RESP3 score stays a float and
    document field keys keep whatever type the client returned.
    """
    response: list = [_map_get(reply, "total_results") or 0]
    results = _map_get(reply, "results") or []

    for item in results:
        fields = _map_get(item, "extra_attributes") if isinstance(item, dict) else None
        if not layout.is_search:
            # FT.AGGREGATE rows carry only extra_attributes: no id, no score.
            response.append(_field_array(fields))
            continue
        response.append(_map_get(item, "id", "") if isinstance(item, dict) else "")
        if layout.with_scores:
            response.append(_map_get(item, "score") if isinstance(item, dict) else None)
        if layout.has_fields:
            response.append(_field_array(fields))
    return response


def _parse_array_reply(
    raw_result: list, layout: _ReplyLayout
) -> tuple[Any, list[dict]]:
    """Parse the flat-array FT.SEARCH / FT.AGGREGATE reply into rows.

    This is the RESP2 wire shape, and the shape ``_fold_map_to_array`` folds a
    RESP3 map into.
    """
    count = raw_result[0] if raw_result else 0
    rows: list[dict] = []

    if not layout.is_search:
        # FT.AGGREGATE format: [count, [fields1], [fields2], ...]
        for row_data in raw_result[1:]:
            row_data = row_data or []
            rows.append(dict(zip(row_data[::2], row_data[1::2])))
        return count, rows

    if layout.with_scores and not layout.has_fields:
        # WITHSCORES + RETURN 0: [count, id1, score1, id2, score2, ...]
        # Stride of 2: key, score (no field array)
        score_alias = _resolve_score_alias(layout.score_alias, layout.args)
        for i in range(1, len(raw_result) - 1, 2):
            rows.append({score_alias: raw_result[i + 1]})
    elif layout.with_scores:
        # WITHSCORES: [count, key1, score1, [fields1], key2, score2, ...]
        # Stride of 3: key, score, field_list
        # First pass: collect all field names across all rows so the alias
        # avoids collisions with any document field, not just the first row's.
        all_field_names: set[str] = set()
        parsed_rows: list[tuple[dict, Any]] = []
        for i in range(1, len(raw_result) - 2, 3):
            score = raw_result[i + 1]
            # A nil field-array (e.g. doc expired mid-query) becomes an empty
            # field set, keeping the row's score instead of crashing.
            row_data = raw_result[i + 2] or []
            row = dict(zip(row_data[::2], row_data[1::2]))
            all_field_names.update(row.keys())
            parsed_rows.append((row, score))
        resolved_alias = _resolve_score_alias(
            layout.score_alias, layout.args, first_row_fields=all_field_names
        )
        for row, score in parsed_rows:
            row[resolved_alias] = score
            rows.append(row)
    else:
        # Standard format: [count, key1, [fields1], key2, [fields2], ...]
        for i in range(2, len(raw_result), 2):
            row_data = raw_result[i] or []
            rows.append(dict(zip(row_data[::2], row_data[1::2])))

    return count, rows


def _parse_reply(raw_result: Any, translated: TranslatedQuery) -> QueryResult:
    """Turn a raw FT.* reply into a QueryResult, RESP2 or RESP3.

    Dispatch is by reply shape rather than by the protocol the connection
    negotiated; see ``docs/for-ais-only/FAILURE_MODES.md`` for why. A reply
    that is neither shape is rejected rather than parsed into empty rows,
    because reading a map that is not a result set (a cluster node-keyed reply,
    FT.PROFILE) or an array that is not one (a WITHCURSOR pair) would otherwise
    silently return nothing.

    FT.HYBRID keeps its own parser: its RESP3 rows are flat field maps, not the
    ``{id, extra_attributes}`` documents FT.SEARCH returns.
    """
    if translated.command == "FT.HYBRID":
        count, rows = _parse_hybrid_reply(raw_result)
        return QueryResult(rows=rows, count=count)

    layout = _ReplyLayout.of(translated)
    if isinstance(raw_result, dict):
        if _map_get(raw_result, "results") is None:
            raise ValueError(
                f"Unrecognized {translated.command} reply: a map without a "
                f"'results' key. Got keys {sorted(map(str, raw_result))}."
            )
        raw_result = _fold_map_to_array(raw_result, layout)
    elif not isinstance(raw_result, list):
        raise ValueError(
            f"Unrecognized {translated.command} reply of type "
            f"{type(raw_result).__name__}; expected an array (RESP2) or a map "
            "(RESP3)."
        )

    count, rows = _parse_array_reply(raw_result, layout)
    return QueryResult(rows=rows, count=count)


class Executor:
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
            _inject_vector_param(cmd, vector_param)

        # Execute command
        try:
            raw_result = self._client.execute_command(*cmd)
        except redis.ResponseError as e:
            error_msg = str(e)
            if translated.command == "FT.HYBRID" and _is_unknown_command(error_msg):
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

        return _parse_reply(raw_result, translated)


class AsyncExecutor:
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
            _inject_vector_param(cmd, vector_param)

        # Execute command asynchronously
        try:
            raw_result = await self._client.execute_command(*cmd)
        except redis.ResponseError as e:
            error_msg = str(e)
            if translated.command == "FT.HYBRID" and _is_unknown_command(error_msg):
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

        return _parse_reply(raw_result, translated)


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
