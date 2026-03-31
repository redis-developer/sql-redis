"""SQL Executor - executes translated queries against Redis."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import redis

from sql_redis.schema import AsyncSchemaRegistry, SchemaRegistry
from sql_redis.translator import Translator

if TYPE_CHECKING:
    import redis.asyncio as async_redis


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

        # Replace $vector placeholder with actual bytes
        if vector_param:
            for i, arg in enumerate(cmd):
                if arg == "$vector":
                    cmd[i] = vector_param

        # Execute command
        try:
            raw_result = self._client.execute_command(*cmd)
        except redis.ResponseError as e:
            error_msg = str(e)
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
        count = raw_result[0] if raw_result else 0
        rows = []

        if translated.command == "FT.SEARCH":
            # Check if WITHSCORES was requested — changes response format
            with_scores = "WITHSCORES" in translated.args
            score_alias = translated.score_alias

            if with_scores:
                # WITHSCORES format: [count, key1, score1, [fields1], key2, score2, [fields2], ...]
                # Stride of 3: key, score, field_list
                for i in range(1, len(raw_result) - 2, 3):
                    score = raw_result[i + 1]
                    row_data = raw_result[i + 2]
                    row = dict(zip(row_data[::2], row_data[1::2]))
                    row[score_alias or "__score"] = score
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

        # Replace $vector placeholder with actual bytes
        if vector_param:
            for i, arg in enumerate(cmd):
                if arg == "$vector":
                    cmd[i] = vector_param

        # Execute command asynchronously
        try:
            raw_result = await self._client.execute_command(*cmd)
        except redis.ResponseError as e:
            error_msg = str(e)
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
        count = raw_result[0] if raw_result else 0
        rows = []

        if translated.command == "FT.SEARCH":
            with_scores = "WITHSCORES" in translated.args
            score_alias = translated.score_alias

            if with_scores:
                # WITHSCORES format: [count, key1, score1, [fields1], ...]
                for i in range(1, len(raw_result) - 2, 3):
                    score = raw_result[i + 1]
                    row_data = raw_result[i + 2]
                    row = dict(zip(row_data[::2], row_data[1::2]))
                    row[score_alias or "__score"] = score
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
