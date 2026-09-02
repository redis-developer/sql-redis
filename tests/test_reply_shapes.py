"""Reply-shape tests for RESP2 arrays and RESP3 maps.

``sql_redis`` sends FT.* commands with ``client.execute_command`` and reads the
raw reply, because redis-py registers no FT.SEARCH / FT.AGGREGATE callback on
the base client. The reply shape therefore follows the negotiated protocol: a
flat array on RESP2, a map on RESP3. The executor folds the map back into the
array shape (``_resp3_to_resp2``) so a single parser handles both.

Every canned reply below was captured from a real Redis 8.4.6 server rather
than hand-written, with the producing cell named in a comment. Reply shapes are
the whole subject here, so an invented literal would only encode the assumption
under test. Replies marked "derived" are a captured reply with one documented
mutation, to reach a case the server does not produce on demand.

The nil-field-array cases for the RESP2 array shape live in
``test_nil_field_array.py``.
"""

import pytest

from sql_redis.translator import TranslatedQuery

# Captured replies.
#
# Cell: redis-py 8.1.0, protocol=3, decode_responses=True, redis:8.4
# SELECT title, price FROM verify_products WHERE category = 'books'
#   ORDER BY price ASC LIMIT 10
RESP3_SEARCH = {
    "attributes": [],
    "format": "STRING",
    "results": [
        {
            "id": "vp:5",
            "extra_attributes": {"price": "32", "title": "redis in action"},
            "values": [],
        },
        {
            "id": "vp:4",
            "extra_attributes": {"price": "39", "title": "sql cookbook"},
            "values": [],
        },
    ],
    "total_results": 2,
    "warning": [],
}

# Cell: redis-py 8.1.0, protocol=3, decode_responses=False, redis:8.4
# Same query. Structural keys are bytes; this is the shape that made redis-py's
# own Result.from_resp3 return zero rows (redis-py#4107).
RESP3_SEARCH_BYTES = {
    b"attributes": [],
    b"format": b"STRING",
    b"results": [
        {
            b"id": b"vp:5",
            b"extra_attributes": {b"price": b"32", b"title": b"redis in action"},
            b"values": [],
        },
        {
            b"id": b"vp:4",
            b"extra_attributes": {b"price": b"39", b"title": b"sql cookbook"},
            b"values": [],
        },
    ],
    b"total_results": 2,
    b"warning": [],
}

# Cell: redis-py 8.1.0, protocol=3, decode_responses=True, redis:8.4
# SELECT title, score() AS relevance FROM verify_products
#   WHERE fulltext(title, 'laptop')
RESP3_SEARCH_WITHSCORES = {
    "attributes": [],
    "format": "STRING",
    "results": [
        {
            "id": "vp:1",
            "score": 0.714285696039395,
            "extra_attributes": {"title": "gaming laptop"},
            "values": [],
        },
        {
            "id": "vp:3",
            "score": 0.714285696039395,
            "extra_attributes": {"title": "laptop stand"},
            "values": [],
        },
    ],
    "total_results": 2,
    "warning": [],
}

# Cell: redis-py 8.1.0, protocol=3, decode_responses=True, redis:8.4
# SELECT score() AS relevance FROM verify_products WHERE fulltext(title, 'laptop')
# RETURN 0 suppresses document fields, so results carry no extra_attributes.
RESP3_SEARCH_SCORE_ONLY = {
    "attributes": [],
    "format": "STRING",
    "results": [
        {"id": "vp:1", "score": 0.714285696039395, "values": []},
        {"id": "vp:3", "score": 0.714285696039395, "values": []},
    ],
    "total_results": 2,
    "warning": [],
}

# Cell: redis-py 8.1.0, protocol=3, decode_responses=True, redis:8.4
# SELECT category, COUNT(*) AS cnt, SUM(price) AS total, AVG(rating) AS avg_rating
#   FROM verify_products GROUP BY category
# Aggregate rows carry only extra_attributes: no id, no score.
RESP3_AGGREGATE = {
    "attributes": [],
    "format": "STRING",
    "results": [
        {
            "extra_attributes": {
                "category": "books",
                "cnt": "2",
                "total": "71",
                "avg_rating": "4.7",
            },
            "values": [],
        },
        {
            "extra_attributes": {
                "category": "office",
                "cnt": "2",
                "total": "67",
                "avg_rating": "3.95",
            },
            "values": [],
        },
    ],
    "total_results": 2,
    "warning": [],
}

# Cell: redis-py 8.1.0, protocol=3, decode_responses=True, redis:8.4
# SELECT title FROM verify_products WHERE category = 'nonexistent'
RESP3_SEARCH_EMPTY = {
    "attributes": [],
    "format": "STRING",
    "results": [],
    "total_results": 0,
    "warning": [],
}

# Derived from RESP3_SEARCH: extra_attributes dropped from the second result,
# the RESP3 analogue of the nil field-array of issue #38 (a document expiring
# between id selection and field materialization).
RESP3_SEARCH_MISSING_FIELDS = {
    "attributes": [],
    "format": "STRING",
    "results": [
        {
            "id": "vp:5",
            "extra_attributes": {"price": "32", "title": "redis in action"},
            "values": [],
        },
        {"id": "vp:4", "values": []},
    ],
    "total_results": 2,
    "warning": [],
}

# Cell: redis-py 8.1.0, protocol=2, decode_responses=True, redis:8.4
# SELECT score() AS relevance FROM verify_products WHERE fulltext(title, 'laptop')
# The one array branch no other test covers, and the only guard on the parser
# having been moved into _parse_array_reply unchanged.
RESP2_SEARCH_SCORE_ONLY = [
    2,
    "vp:1",
    "0.714285696039395",
    "vp:3",
    "0.714285696039395",
]


# Translations matching those captures.
def _search() -> TranslatedQuery:
    return TranslatedQuery(
        command="FT.SEARCH",
        index="verify_products",
        query_string="@category:{books}",
        args=["RETURN", "2", "title", "price", "LIMIT", "0", "10", "DIALECT", "2"],
    )


def _search_withscores() -> TranslatedQuery:
    return TranslatedQuery(
        command="FT.SEARCH",
        index="verify_products",
        query_string="@title:laptop",
        args=["RETURN", "1", "title", "WITHSCORES", "SCORER", "BM25", "DIALECT", "2"],
        score_alias="relevance",
    )


def _search_score_only() -> TranslatedQuery:
    return TranslatedQuery(
        command="FT.SEARCH",
        index="verify_products",
        query_string="@title:laptop",
        args=["RETURN", "0", "WITHSCORES", "SCORER", "BM25", "DIALECT", "2"],
        score_alias="relevance",
    )


def _aggregate() -> TranslatedQuery:
    return TranslatedQuery(
        command="FT.AGGREGATE",
        index="verify_products",
        query_string="*",
        args=["GROUPBY", "1", "@category", "REDUCE", "COUNT", "0", "AS", "cnt"],
    )


@pytest.fixture(params=["sync", "async"])
def execute_reply(request, stub_executor, stub_async_executor):
    """Run one SQL query through either executor against a canned reply.

    Parametrized so each test is written once and asserted for both the sync
    and async executors, whose parse paths are separate call sites.
    """
    flavour = request.param

    async def _run(translated: TranslatedQuery, raw_result):
        sql = f"SELECT * FROM {translated.index}"
        if flavour == "sync":
            return stub_executor(translated, raw_result).execute(sql)
        return await stub_async_executor(translated, raw_result).execute(sql)

    return _run


class TestSearchResp3Map:
    """A RESP3 FT.SEARCH map parses to the same rows as the RESP2 array."""

    async def test_standard_search_map_reply_yields_field_rows(self, execute_reply):
        result = await execute_reply(_search(), RESP3_SEARCH)

        assert result.count == 2
        assert result.rows == [
            {"price": "32", "title": "redis in action"},
            {"price": "39", "title": "sql cookbook"},
        ]

    async def test_structural_keys_may_be_bytes(self, execute_reply):
        """A decode_responses=False client sends bytes structural keys.

        Document field keys stay bytes, matching the RESP2 path; looking up
        ``extra_attributes`` as str alone would yield silently empty rows.
        """
        result = await execute_reply(_search(), RESP3_SEARCH_BYTES)

        assert result.count == 2
        assert result.rows == [
            {b"price": b"32", b"title": b"redis in action"},
            {b"price": b"39", b"title": b"sql cookbook"},
        ]

    async def test_bookkeeping_keys_do_not_leak_into_rows(self, execute_reply):
        """``id``, ``values`` and ``extra_attributes`` are not columns."""
        result = await execute_reply(_search(), RESP3_SEARCH)

        for row in result.rows:
            assert not {"id", "values", "extra_attributes", "payload"} & set(row)

    async def test_missing_extra_attributes_yields_empty_row(self, execute_reply):
        result = await execute_reply(_search(), RESP3_SEARCH_MISSING_FIELDS)

        assert result.count == 2
        assert result.rows == [{"price": "32", "title": "redis in action"}, {}]

    async def test_empty_results_yields_no_rows(self, execute_reply):
        result = await execute_reply(_search(), RESP3_SEARCH_EMPTY)

        assert result.count == 0
        assert result.rows == []


class TestSearchWithScoresResp3Map:
    """The score comes from the result's own ``score`` key, not a stride."""

    async def test_withscores_map_reply_adds_score_column(self, execute_reply):
        result = await execute_reply(_search_withscores(), RESP3_SEARCH_WITHSCORES)

        assert result.count == 2
        assert result.rows == [
            {"title": "gaming laptop", "relevance": 0.714285696039395},
            {"title": "laptop stand", "relevance": 0.714285696039395},
        ]

    async def test_score_stays_a_float(self, execute_reply):
        """RESP3 scores are doubles and are passed through, not stringified.

        RESP2 delivers the score as text, so the type differs by protocol. The
        library does not normalise it: ``float()`` is the protocol-safe
        conversion at the call site. See docs/concepts/result-shape.md.
        """
        result = await execute_reply(_search_withscores(), RESP3_SEARCH_WITHSCORES)

        assert isinstance(result.rows[0]["relevance"], float)

    async def test_score_alias_collision_is_resolved(self, execute_reply):
        """An alias colliding with a document field is renamed, as on RESP2."""
        translated = _search_withscores()
        translated.score_alias = "title"

        result = await execute_reply(translated, RESP3_SEARCH_WITHSCORES)

        assert result.rows[0]["title"] == "gaming laptop"
        assert result.rows[0]["__score_title"] == 0.714285696039395

    async def test_withscores_return0_map_reply_yields_score_only_rows(
        self, execute_reply
    ):
        result = await execute_reply(_search_score_only(), RESP3_SEARCH_SCORE_ONLY)

        assert result.count == 2
        assert result.rows == [
            {"relevance": 0.714285696039395},
            {"relevance": 0.714285696039395},
        ]


class TestAggregateResp3Map:
    """A RESP3 FT.AGGREGATE map parses to rows of reduced values."""

    async def test_aggregate_map_reply_yields_rows(self, execute_reply):
        result = await execute_reply(_aggregate(), RESP3_AGGREGATE)

        assert result.count == 2
        assert result.rows == [
            {"category": "books", "cnt": "2", "total": "71", "avg_rating": "4.7"},
            {"category": "office", "cnt": "2", "total": "67", "avg_rating": "3.95"},
        ]

    async def test_aggregate_count_comes_from_total_results(self, execute_reply):
        """RESP3 ``total_results`` matches the RESP2 leading count.

        Measured against Redis 8.4.6: a GROUP BY returning N groups reports
        ``total_results: N`` on RESP3 and ``N`` at position 0 on RESP2.
        """
        result = await execute_reply(_aggregate(), RESP3_AGGREGATE)

        assert result.count == len(result.rows) == 2


class TestResp2ArrayUnchanged:
    """The array path still parses the shape it always did."""

    async def test_withscores_return0_array_reply(self, execute_reply):
        result = await execute_reply(_search_score_only(), RESP2_SEARCH_SCORE_ONLY)

        assert result.count == 2
        assert result.rows == [
            {"relevance": "0.714285696039395"},
            {"relevance": "0.714285696039395"},
        ]
