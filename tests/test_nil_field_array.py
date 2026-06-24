"""Regression tests for issue #38.

The ``FT.SEARCH`` / ``FT.AGGREGATE`` reply parser used to slice each
per-document field-array directly. When a field-array came back as ``None``
(e.g. a document expiring between id selection and field materialization),
``dict(zip(row_data[::2], row_data[1::2]))`` raised
``TypeError: 'NoneType' object is not subscriptable`` and the whole query
failed.

These tests feed crafted replies (with a nil field-array) straight through the
parser by mocking the translator and client, so every parse branch is exercised
deterministically without a live Redis server.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from sql_redis.executor import AsyncExecutor, Executor
from sql_redis.translator import TranslatedQuery


def _make_sync_executor(translated: TranslatedQuery, raw_result):
    """Build an Executor whose translation and Redis reply are stubbed."""
    executor = Executor.__new__(Executor)
    executor._client = MagicMock()
    executor._client.execute_command.return_value = raw_result
    executor._schema_registry = MagicMock()
    executor._translator = MagicMock()
    executor._translator.translate.return_value = translated
    return executor


def _make_async_executor(translated: TranslatedQuery, raw_result):
    """Build an AsyncExecutor whose translation and Redis reply are stubbed."""
    executor = AsyncExecutor.__new__(AsyncExecutor)
    executor._client = MagicMock()
    executor._client.execute_command = AsyncMock(return_value=raw_result)
    executor._schema_registry = MagicMock()
    executor._schema_registry.ensure_schema = AsyncMock()
    executor._translator = MagicMock()
    # AsyncExecutor.execute parses first, then translates the parsed result.
    parsed = MagicMock()
    parsed.index = None  # skip ensure_schema()
    executor._translator.parse.return_value = parsed
    executor._translator.translate_parsed.return_value = translated
    return executor


class TestStandardSearchNilFields:
    """Standard FT.SEARCH reply: [count, key1, [fields1], key2, [fields2], ...]."""

    def _translated(self) -> TranslatedQuery:
        return TranslatedQuery(
            command="FT.SEARCH",
            index="products",
            query_string="*",
        )

    def test_sync_tolerates_nil_field_array(self):
        # Second document's field-array came back nil.
        raw_result = [2, "product:1", ["title", "Laptop"], "product:2", None]
        executor = _make_sync_executor(self._translated(), raw_result)

        result = executor.execute("SELECT * FROM products")

        assert result.count == 2
        assert result.rows == [{"title": "Laptop"}, {}]

    async def test_async_tolerates_nil_field_array(self):
        raw_result = [2, "product:1", ["title", "Laptop"], "product:2", None]
        executor = _make_async_executor(self._translated(), raw_result)

        result = await executor.execute("SELECT * FROM products")

        assert result.count == 2
        assert result.rows == [{"title": "Laptop"}, {}]


class TestWithScoresNilFields:
    """WITHSCORES reply: [count, key1, score1, [fields1], ...] (score_alias set)."""

    def _translated(self) -> TranslatedQuery:
        return TranslatedQuery(
            command="FT.SEARCH",
            index="products",
            query_string="*",
            score_alias="score",
        )

    def test_sync_keeps_score_when_fields_nil(self):
        raw_result = [
            2,
            "product:1",
            "0.5",
            ["title", "Laptop"],
            "product:2",
            "0.9",
            None,
        ]
        executor = _make_sync_executor(self._translated(), raw_result)

        result = executor.execute("SELECT * FROM products")

        assert result.count == 2
        assert result.rows == [
            {"title": "Laptop", "score": "0.5"},
            {"score": "0.9"},
        ]

    async def test_async_keeps_score_when_fields_nil(self):
        raw_result = [
            2,
            "product:1",
            "0.5",
            ["title", "Laptop"],
            "product:2",
            "0.9",
            None,
        ]
        executor = _make_async_executor(self._translated(), raw_result)

        result = await executor.execute("SELECT * FROM products")

        assert result.count == 2
        assert result.rows == [
            {"title": "Laptop", "score": "0.5"},
            {"score": "0.9"},
        ]


class TestAggregateNilFields:
    """FT.AGGREGATE reply: [count, [fields1], [fields2], ...]."""

    def _translated(self) -> TranslatedQuery:
        return TranslatedQuery(
            command="FT.AGGREGATE",
            index="products",
            query_string="*",
        )

    def test_sync_tolerates_nil_row(self):
        raw_result = [2, ["category", "books"], None]
        executor = _make_sync_executor(self._translated(), raw_result)

        result = executor.execute("SELECT category FROM products GROUP BY category")

        assert result.count == 2
        assert result.rows == [{"category": "books"}, {}]

    async def test_async_tolerates_nil_row(self):
        raw_result = [2, ["category", "books"], None]
        executor = _make_async_executor(self._translated(), raw_result)

        result = await executor.execute(
            "SELECT category FROM products GROUP BY category"
        )

        assert result.count == 2
        assert result.rows == [{"category": "books"}, {}]
