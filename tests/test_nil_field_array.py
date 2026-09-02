"""Regression tests for issue #38.

The ``FT.SEARCH`` / ``FT.AGGREGATE`` reply parser used to slice each
per-document field-array directly. When a field-array came back as ``None``
(e.g. a document expiring between id selection and field materialization),
``dict(zip(row_data[::2], row_data[1::2]))`` raised
``TypeError: 'NoneType' object is not subscriptable`` and the whole query
failed.

These tests feed crafted replies (with a nil field-array) straight through the
parser by mocking the translator and client, so every parse branch is exercised
deterministically without a live Redis server. The ``stub_executor`` /
``stub_async_executor`` factory fixtures live in ``conftest.py``; the RESP3
equivalents of these cases are in ``test_reply_shapes.py``.
"""

from sql_redis.translator import TranslatedQuery


class TestStandardSearchNilFields:
    """Standard FT.SEARCH reply: [count, key1, [fields1], key2, [fields2], ...]."""

    def _translated(self) -> TranslatedQuery:
        return TranslatedQuery(
            command="FT.SEARCH",
            index="products",
            query_string="*",
        )

    def test_sync_tolerates_nil_field_array(self, stub_executor):
        # Second document's field-array came back nil.
        raw_result = [2, "product:1", ["title", "Laptop"], "product:2", None]
        executor = stub_executor(self._translated(), raw_result)

        result = executor.execute("SELECT * FROM products")

        assert result.count == 2
        assert result.rows == [{"title": "Laptop"}, {}]

    async def test_async_tolerates_nil_field_array(self, stub_async_executor):
        raw_result = [2, "product:1", ["title", "Laptop"], "product:2", None]
        executor = stub_async_executor(self._translated(), raw_result)

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

    def test_sync_keeps_score_when_fields_nil(self, stub_executor):
        raw_result = [
            2,
            "product:1",
            "0.5",
            ["title", "Laptop"],
            "product:2",
            "0.9",
            None,
        ]
        executor = stub_executor(self._translated(), raw_result)

        result = executor.execute("SELECT * FROM products")

        assert result.count == 2
        assert result.rows == [
            {"title": "Laptop", "score": "0.5"},
            {"score": "0.9"},
        ]

    async def test_async_keeps_score_when_fields_nil(self, stub_async_executor):
        raw_result = [
            2,
            "product:1",
            "0.5",
            ["title", "Laptop"],
            "product:2",
            "0.9",
            None,
        ]
        executor = stub_async_executor(self._translated(), raw_result)

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

    def test_sync_tolerates_nil_row(self, stub_executor):
        raw_result = [2, ["category", "books"], None]
        executor = stub_executor(self._translated(), raw_result)

        result = executor.execute("SELECT category FROM products GROUP BY category")

        assert result.count == 2
        assert result.rows == [{"category": "books"}, {}]

    async def test_async_tolerates_nil_row(self, stub_async_executor):
        raw_result = [2, ["category", "books"], None]
        executor = stub_async_executor(self._translated(), raw_result)

        result = await executor.execute(
            "SELECT category FROM products GROUP BY category"
        )

        assert result.count == 2
        assert result.rows == [{"category": "books"}, {}]
