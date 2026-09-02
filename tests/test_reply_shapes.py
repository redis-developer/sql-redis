"""Reply-shape tests for RESP2 arrays and RESP3 maps.

The reply shape follows the protocol the client negotiated: a flat array on
RESP2, a map on RESP3. See ``docs/for-ais-only/FAILURE_MODES.md`` for why the
executor sees the raw shape at all.

Every canned reply below was captured from a real Redis 8.4.6 server rather
than hand-written, with the producing cell named in a comment. Reply shapes are
the whole subject here, so an invented literal would only encode the assumption
under test. Replies marked "derived" are a captured reply with one documented
mutation, to reach a case the server does not produce on demand.

``test_protocol_matrix.py`` proves these are the shapes Redis really sends; the
nil-field-array cases for the array shape live in ``test_nil_field_array.py``.
"""

import pytest
import redis

pytestmark = pytest.mark.protocol

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

# Cell: redis-py 8.1.0, protocol=3, decode_responses=True, redis:8.4
# SELECT title FROM cnt_idx ORDER BY price ASC LIMIT 2, over 5 matching documents.
# total_results is the total match count, not the number of rows returned, so
# this is the capture that catches count being derived from len(results).
RESP3_SEARCH_LIMITED = {
    "attributes": [],
    "format": "STRING",
    "results": [
        {"id": "ci:0", "extra_attributes": {"title": "book 0"}, "values": []},
        {"id": "ci:1", "extra_attributes": {"title": "book 1"}, "values": []},
    ],
    "total_results": 5,
    "warning": [],
}

# Cell: redis-py 8.1.0, protocol=2, decode_responses=True, redis:8.4
# Same query. RESP2 puts the same total at position 0.
RESP2_SEARCH_LIMITED = [
    5,
    "ci:0",
    ["title", "book 0"],
    "ci:1",
    ["title", "book 1"],
]

# Cells: redis-py 8.1.0, protocol=2 and protocol=3, decode_responses=True,
# redis:8.4. SELECT price * 2 AS dbl FROM cnt_idx, which translates to
# FT.AGGREGATE ... APPLY with no GROUPBY. RESP2 sends the placeholder 1 that
# the Redis docs disclaim as "not a valid value"; RESP3 sends the real count.
# See TestAggregateCountDivergesByProtocol.
RESP2_AGGREGATE_COMPUTED = [
    1,
    ["price", "10", "dbl", "20"],
    ["price", "11", "dbl", "22"],
]

RESP3_AGGREGATE_COMPUTED = {
    "attributes": [],
    "format": "STRING",
    "results": [
        {"extra_attributes": {"price": "10", "dbl": "20"}, "values": []},
        {"extra_attributes": {"price": "11", "dbl": "22"}, "values": []},
    ],
    "total_results": 2,
    "warning": [],
}

# Cell: redis-py 8.1.0, protocol=2, decode_responses=True, redis:8.4
# SELECT score() AS relevance FROM verify_products WHERE fulltext(title, 'laptop')
# The one array branch no other test covers.
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


def _search_limited() -> TranslatedQuery:
    return TranslatedQuery(
        command="FT.SEARCH",
        index="cnt_idx",
        query_string="*",
        args=["RETURN", "1", "title", "LIMIT", "0", "2", "DIALECT", "2"],
    )


def _aggregate_computed() -> TranslatedQuery:
    return TranslatedQuery(
        command="FT.AGGREGATE",
        index="cnt_idx",
        query_string="*",
        args=["APPLY", "@price * 2", "AS", "dbl", "DIALECT", "2"],
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

    async def test_only_document_fields_become_columns(self, execute_reply):
        """A new RESP3 result key must not become a column.

        Asserted as an allowlist rather than a denylist of known bookkeeping
        keys, so a key RediSearch adds later fails here instead of silently
        appearing in every row.
        """
        result = await execute_reply(_search(), RESP3_SEARCH)

        for row in result.rows:
            assert set(row) == {"price", "title"}

    async def test_count_is_the_total_not_the_row_count(self, execute_reply):
        """``count`` comes from ``total_results``, which LIMIT does not reduce.

        Callers paginate on this (docs/concepts/result-shape.md), so deriving
        it from the number of returned results would be silently wrong.
        """
        result = await execute_reply(_search_limited(), RESP3_SEARCH_LIMITED)

        assert result.count == 5
        assert len(result.rows) == 2

    async def test_missing_extra_attributes_yields_empty_row(self, execute_reply):
        result = await execute_reply(_search(), RESP3_SEARCH_MISSING_FIELDS)

        assert result.count == 2
        assert result.rows == [{"price": "32", "title": "redis in action"}, {}]

    async def test_flat_field_array_is_tolerated(self, execute_reply):
        """Defensive: a result whose fields arrive already flat still parses.

        No redis-py version sends this for FT.SEARCH, but ``_parse_hybrid_reply``
        accepts both forms for the same conceptual field, and a total
        ``_field_array`` is what keeps an unexpected shape from silently
        becoming an empty row. Derived from RESP3_SEARCH.
        """
        reply = {
            "attributes": [],
            "format": "STRING",
            "results": [{"id": "vp:5", "extra_attributes": ["title", "flat"]}],
            "total_results": 1,
            "warning": [],
        }

        result = await execute_reply(_search(), reply)

        assert result.rows == [{"title": "flat"}]

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

    async def test_missing_extra_attributes_yields_empty_row(self, execute_reply):
        """The aggregate branch reaches the field flattener separately."""
        reply = {
            "attributes": [],
            "format": "STRING",
            "results": [{"extra_attributes": {"category": "books"}}, {"values": []}],
            "total_results": 2,
            "warning": [],
        }

        result = await execute_reply(_aggregate(), reply)

        assert result.rows == [{"category": "books"}, {}]


class TestAggregateCountDivergesByProtocol:
    """For FT.AGGREGATE, ``count`` is protocol-dependent, and that is accepted.

    An aggregate pipeline with no GROUPBY (a computed field, a date function, a
    geo_distance projection) leaves RESP2's leading integer as the placeholder
    the Redis docs call "not a valid value", while RESP3's ``total_results``
    reports the real figure. Measured on Redis 8.4.6: the same SQL over 5
    documents gives count=1 on RESP2 and count=5 on RESP3.

    Neither value can be turned into the other without changing the protocol=2
    behaviour callers already depend on, so both are surfaced as Redis sent
    them. This test exists to stop someone "fixing" the difference by deriving
    count from len(rows), which would silently change RESP2 output.
    """

    async def test_resp2_reports_the_placeholder(self, execute_reply):
        result = await execute_reply(_aggregate_computed(), RESP2_AGGREGATE_COMPUTED)

        assert result.count == 1
        assert len(result.rows) == 2

    async def test_resp3_reports_the_real_total(self, execute_reply):
        result = await execute_reply(_aggregate_computed(), RESP3_AGGREGATE_COMPUTED)

        assert result.count == 2
        assert len(result.rows) == 2


class TestResp2ArrayUnchanged:
    """The array path still parses the shape it always did."""

    async def test_count_is_the_total_not_the_row_count(self, execute_reply):
        result = await execute_reply(_search_limited(), RESP2_SEARCH_LIMITED)

        assert result.count == 5
        assert len(result.rows) == 2

    async def test_withscores_return0_array_reply(self, execute_reply):
        result = await execute_reply(_search_score_only(), RESP2_SEARCH_SCORE_ONLY)

        assert result.count == 2
        assert result.rows == [
            {"relevance": "0.714285696039395"},
            {"relevance": "0.714285696039395"},
        ]


class TestUnrecognizedReplies:
    """A reply that is neither shape is rejected, not parsed into empty rows.

    A map that is not a result set (a cluster node-keyed reply, FT.PROFILE) or
    an array that is not one (a WITHCURSOR pair) would otherwise fold to zero
    rows and look like an empty result set. That silent-empty failure is the
    one redis-py shipped in 8.0.0 (redis-py#4107).
    """

    async def test_map_without_results_raises(self, execute_reply):
        node_keyed = {"127.0.0.1:6379": {"total_results": 2, "results": []}}

        with pytest.raises(ValueError, match="without a 'results' key"):
            await execute_reply(_search(), node_keyed)

    async def test_profile_shaped_map_raises(self, execute_reply):
        with pytest.raises(ValueError, match="without a 'results' key"):
            await execute_reply(_search(), {"Results": {}, "Profile": {}})

    async def test_non_array_non_map_reply_raises(self, execute_reply):
        with pytest.raises(ValueError, match="Unrecognized FT.SEARCH reply"):
            await execute_reply(_search(), "OK")

    async def test_empty_results_list_is_not_rejected(self, execute_reply):
        """An empty result set is legitimate and must still parse."""
        result = await execute_reply(_search(), RESP3_SEARCH_EMPTY)

        assert result.count == 0
        assert result.rows == []


class TestErrorRewrapping:
    """Both executors re-wrap ResponseError with an actionable hint."""

    async def test_ismissing_hint_is_added(self, execute_reply):
        translated = TranslatedQuery(
            command="FT.SEARCH",
            index="verify_products",
            query_string="ismissing(@email)",
            args=["DIALECT", "2"],
        )
        error = redis.ResponseError("Unknown function")

        with pytest.raises(redis.ResponseError, match="ismissing\\(\\) requires"):
            await execute_reply(translated, error)

    async def test_unrelated_response_error_propagates_unchanged(self, execute_reply):
        """Only the two known-cause errors are re-wrapped."""
        error = redis.ResponseError("no such index")

        with pytest.raises(redis.ResponseError, match="^no such index$"):
            await execute_reply(_search(), error)

    async def test_hybrid_version_hint_is_added(self, execute_reply):
        translated = TranslatedQuery(
            command="FT.HYBRID",
            index="verify_items",
            query_string="",
            args=["SEARCH", "@description:smartphone"],
            is_hybrid=True,
        )
        error = redis.ResponseError("unknown command 'FT.HYBRID'")

        with pytest.raises(redis.ResponseError, match="8.4"):
            await execute_reply(translated, error)
