"""SQL Executor - executes translated queries against Redis."""

from __future__ import annotations

from dataclasses import dataclass

import redis

from sql_redis.schema import SchemaRegistry
from sql_redis.translator import Translator


@dataclass
class QueryResult:
    """Result of executing a SQL query."""

    rows: list[dict]
    count: int


class Executor:
    """Executes SQL queries against Redis."""

    def __init__(self, client: redis.Redis, schema_registry: SchemaRegistry):
        """Initialize executor with Redis client and schema registry."""
        self._client = client
        self._schema_registry = schema_registry
        self._translator = Translator(schema_registry)

    def execute(self, sql: str, *, params: dict | None = None) -> QueryResult:
        """Execute a SQL query and return results."""
        params = params or {}

        # Substitute non-bytes params in SQL
        for key, value in params.items():
            placeholder = f":{key}"
            if isinstance(value, (int, float)):
                sql = sql.replace(placeholder, str(value))
            elif isinstance(value, str):
                sql = sql.replace(placeholder, f"'{value}'")
            # bytes (vectors) are handled via Redis PARAMS

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
        raw_result = self._client.execute_command(*cmd)

        # Parse result based on command type
        count = raw_result[0] if raw_result else 0
        rows = []

        if translated.command == "FT.SEARCH":
            # FT.SEARCH format: [count, key1, [fields1], key2, [fields2], ...]
            # Skip document keys (odd indices), take field lists (even indices after count)
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
