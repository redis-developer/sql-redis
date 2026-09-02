"""End-to-end tests across RESP protocols and redis-py client modes.

``sql_redis`` reads raw FT.* replies, so the shape it parses is decided by the
negotiated wire protocol rather than by anything the library configures. Four
client modes matter, not two:

* ``protocol=2`` - flat arrays, the historical behaviour
* ``protocol`` unset - RESP2 on redis-py 6.x/7.x, RESP3 from redis-py 8
* ``protocol=3`` - RESP3 maps on every version
* ``legacy_responses=False`` - RESP3, redis-py 8 only

These tests assert against a real server, because reply shapes are the subject
and a mock would encode the assumption under test. ``test_reply_shapes.py``
pins the exact map literals; this module proves those are the shapes Redis
actually sends and that the rows come out identical either way.

The fixtures here are deliberately local. Parametrizing the shared
``redis_client`` fixture in ``conftest.py`` would multiply thirteen other
modules, and three of them assert raw positional replies against that client.
"""

import pytest
import redis
import redis.asyncio as async_redis

from sql_redis import create_async_executor, create_executor

INDEX = "protocol_matrix"
PREFIX = "pm:"

ROWS = [
    ("pm:1", {"title": "gaming laptop", "category": "electronics", "price": 1499}),
    ("pm:2", {"title": "laptop stand", "category": "office", "price": 45}),
    ("pm:3", {"title": "sql cookbook", "category": "books", "price": 39}),
    ("pm:4", {"title": "redis in action", "category": "books", "price": 32}),
]

PLAIN_SQL = (
    f"SELECT title, price FROM {INDEX} WHERE category = 'books' ORDER BY price ASC"
)
AGGREGATE_SQL = f"SELECT category, COUNT(*) AS cnt, SUM(price) AS total FROM {INDEX} GROUP BY category"
SCORE_SQL = (
    f"SELECT title, score() AS relevance FROM {INDEX} WHERE fulltext(title, 'laptop')"
)

_REDIS_PY_MAJOR = int(redis.__version__.split(".")[0])

CLIENT_MODES = [
    pytest.param({"protocol": 2}, id="protocol-2"),
    pytest.param({}, id="default"),
    pytest.param({"protocol": 3}, id="protocol-3"),
    pytest.param(
        {"legacy_responses": False},
        id="new-response-format",
        marks=pytest.mark.skipif(
            _REDIS_PY_MAJOR < 8, reason="legacy_responses requires redis-py 8"
        ),
    ),
]


@pytest.fixture(scope="module")
def endpoint(redis_container) -> tuple[str, int]:
    """Host and port of the shared test container."""
    return (
        redis_container.get_container_host_ip(),
        int(redis_container.get_exposed_port(6379)),
    )


@pytest.fixture(scope="module")
def matrix_index(endpoint) -> str:
    """Create the index and load its documents over a plain RESP2 client.

    Setup deliberately does not depend on the protocol under test, so the data
    is materialized once no matter how many client modes are exercised.
    """
    host, port = endpoint
    client = redis.Redis(host=host, port=port, protocol=2, decode_responses=True)
    try:
        client.execute_command("FT.DROPINDEX", INDEX, "DD")
    except redis.ResponseError:
        pass
    client.execute_command(
        "FT.CREATE",
        INDEX,
        "ON",
        "HASH",
        "PREFIX",
        "1",
        PREFIX,
        "SCHEMA",
        "title",
        "TEXT",
        "SORTABLE",
        "category",
        "TAG",
        "SORTABLE",
        "price",
        "NUMERIC",
        "SORTABLE",
    )
    for key, row in ROWS:
        client.hset(key, mapping=row)
    yield INDEX
    try:
        client.execute_command("FT.DROPINDEX", INDEX, "DD")
    except redis.ResponseError:
        pass
    client.close()


def _client(endpoint, **kwargs) -> redis.Redis:
    host, port = endpoint
    return redis.Redis(host=host, port=port, decode_responses=True, **kwargs)


def _rows(endpoint, sql, **kwargs) -> tuple[list[dict], int]:
    client = _client(endpoint, **kwargs)
    try:
        result = create_executor(client).execute(sql)
        return result.rows, result.count
    finally:
        client.close()


class TestReplyShapeCanary:
    """The protocols really do send different shapes.

    Without this, the redis-py compatibility CI job could report green having
    never produced a map at all.
    """

    @pytest.mark.parametrize("protocol,expected", [(2, list), (3, dict)])
    @pytest.mark.parametrize("command", ["FT.SEARCH", "FT.AGGREGATE"])
    def test_raw_reply_type_follows_protocol(
        self, endpoint, matrix_index, protocol, expected, command
    ):
        client = _client(endpoint, protocol=protocol)
        try:
            if command == "FT.SEARCH":
                raw = client.execute_command(
                    command, matrix_index, "*", "LIMIT", "0", "2"
                )
            else:
                raw = client.execute_command(
                    command, matrix_index, "*", "GROUPBY", "1", "@category"
                )
        finally:
            client.close()

        assert isinstance(raw, expected)


class TestRowsMatchAcrossClientModes:
    """Every client mode yields the rows the protocol=2 baseline yields."""

    @pytest.mark.parametrize("client_kwargs", CLIENT_MODES)
    def test_search_rows_match(self, endpoint, matrix_index, client_kwargs):
        baseline, baseline_count = _rows(endpoint, PLAIN_SQL, protocol=2)
        rows, count = _rows(endpoint, PLAIN_SQL, **client_kwargs)

        assert (
            rows
            == baseline
            == [
                {"title": "redis in action", "price": "32"},
                {"title": "sql cookbook", "price": "39"},
            ]
        )
        assert count == baseline_count == 2

    @pytest.mark.parametrize("client_kwargs", CLIENT_MODES)
    def test_aggregate_rows_match(self, endpoint, matrix_index, client_kwargs):
        baseline, baseline_count = _rows(endpoint, AGGREGATE_SQL, protocol=2)
        rows, count = _rows(endpoint, AGGREGATE_SQL, **client_kwargs)

        by_category = {row["category"]: row for row in rows}
        assert sorted(by_category) == ["books", "electronics", "office"]
        assert by_category["books"] == {"category": "books", "cnt": "2", "total": "71"}
        assert rows == baseline
        assert count == baseline_count == 3

    @pytest.mark.parametrize("client_kwargs", CLIENT_MODES)
    def test_no_bookkeeping_columns_leak(self, endpoint, matrix_index, client_kwargs):
        """RESP3 result maps carry id/values/extra_attributes; rows must not."""
        rows, _ = _rows(endpoint, PLAIN_SQL, **client_kwargs)

        for row in rows:
            assert not {"id", "values", "extra_attributes", "payload"} & set(row)


class TestScoreValueType:
    """The score's type differs by protocol, and that is accepted."""

    def test_score_type_diverges_by_protocol(self, endpoint, matrix_index):
        resp2_rows, _ = _rows(endpoint, SCORE_SQL, protocol=2)
        resp3_rows, _ = _rows(endpoint, SCORE_SQL, protocol=3)

        assert isinstance(resp2_rows[0]["relevance"], str)
        assert isinstance(resp3_rows[0]["relevance"], float)
        # float() is the protocol-safe conversion at the call site.
        assert [float(r["relevance"]) for r in resp2_rows] == [
            float(r["relevance"]) for r in resp3_rows
        ]
        assert [r["title"] for r in resp2_rows] == [r["title"] for r in resp3_rows]


class TestDecodeResponsesOff:
    """A bytes-keyed RESP3 map is the shape that broke redis-py itself."""

    def test_bytes_keys_still_yield_rows(self, endpoint, matrix_index):
        host, port = endpoint
        client = redis.Redis(host=host, port=port, protocol=3)
        try:
            result = create_executor(client).execute(PLAIN_SQL)
        finally:
            client.close()

        assert result.count == 2
        assert result.rows == [
            {b"title": b"redis in action", b"price": b"32"},
            {b"title": b"sql cookbook", b"price": b"39"},
        ]


class TestAsyncAcrossProtocols:
    """The async executor has its own parse call site."""

    @pytest.mark.parametrize("protocol", [2, 3])
    async def test_async_search_and_aggregate(self, endpoint, matrix_index, protocol):
        host, port = endpoint
        client = async_redis.Redis(
            host=host, port=port, protocol=protocol, decode_responses=True
        )
        try:
            executor = await create_async_executor(client)
            search = await executor.execute(PLAIN_SQL)
            aggregate = await executor.execute(AGGREGATE_SQL)
        finally:
            await client.aclose()

        assert search.count == 2
        assert search.rows == [
            {"title": "redis in action", "price": "32"},
            {"title": "sql cookbook", "price": "39"},
        ]
        assert aggregate.count == 3
        assert {row["category"] for row in aggregate.rows} == {
            "books",
            "electronics",
            "office",
        }
