"""TDD tests for FT.HYBRID support via the hybrid_vector_search() function.

These tests are written ahead of the implementation (RAAE-1322). They define the
contract for translating

    hybrid_vector_search(<vector_leg>, <text_leg>, <combine>)

into a native ``FT.HYBRID`` command (Redis 8.4+), which fuses an independently
ranked text search and vector search server-side (RRF or LINEAR). This is distinct
from the existing pre-filter hybrid search, where text is only a hard prefilter and
the ranking comes from the vector leg alone.

Expected (not yet implemented) API contract
--------------------------------------------
``ParsedQuery.hybrid_search``: ``HybridSearchSpec | None``, populated when the SELECT
projection contains ``hybrid_vector_search(...)``. The function composes the existing
``cosine_distance(field, :vec)`` (vector leg) and ``fulltext(field, 'query')`` (text
leg) functions, plus a third ``rrf(...)`` / ``linear(...)`` argument for fusion.

``HybridSearchSpec`` fields:
    vector_field: str            # VSIM field
    text_field: str              # SEARCH field
    text_query: str              # SEARCH query string
    text_scorer: str = "BM25STD" # SEARCH scorer
    vector_method: str = "KNN"   # "KNN" | "RANGE"
    ef_runtime: int | None       # KNN tuning knob
    radius / epsilon: float|None # RANGE knobs
    combine_method: str = "RRF"  # "RRF" | "LINEAR"
    rrf_constant: int | None     # RRF knob (default 60)
    rrf_window: int | None       # RRF/LINEAR window knob (default 20)
    linear_alpha: float | None   # LINEAR knob; beta derived as (1 - alpha)
    alias: str                   # combined-score column (the SELECT alias)
    k: int | None                # KNN K, derived from LIMIT

``AnalyzedQuery.hybrid_search``: ``HybridSearchAnalysis | None`` with field types
resolved. ``Translator.translate(...)`` returns a ``TranslatedQuery`` whose
``command == "FT.HYBRID"``.
"""

import struct

import pytest
import redis

from sql_redis.analyzer import Analyzer
from sql_redis.executor import Executor
from sql_redis.parser import SQLParser
from sql_redis.schema import SchemaRegistry
from sql_redis.translator import Translator

pytestmark = pytest.mark.protocol


def float_vector_to_bytes(vector: list[float]) -> bytes:
    """Convert a list of floats to binary format for Redis vector storage."""
    return struct.pack(f"{len(vector)}f", *vector)


def _hybrid_supported(client: redis.Redis) -> bool:
    """Return True if the connected server understands FT.HYBRID (Redis 8.4+)."""
    try:
        client.execute_command("FT.HYBRID")
    except redis.ResponseError as exc:
        message = str(exc).lower()
        # No args -> arity/syntax error means the command exists; only an
        # "unknown command" reply means the server is too old.
        return "unknown command" not in message and "unknown subcommand" not in message
    return True


@pytest.fixture
def sample_schema() -> dict[str, dict[str, str]]:
    """Schema with text, tag, vector, and numeric fields for hybrid tests."""
    return {
        "items": {
            "name": "TEXT",
            "category": "TAG",
            "description": "TEXT",
            "price": "NUMERIC",
            "embedding": "VECTOR",
        }
    }


@pytest.fixture(scope="module")
def hybrid_translator(redis_client: redis.Redis, items_index: str) -> Translator:
    """Translator with the items index (text + tag + vector) loaded."""
    registry = SchemaRegistry(redis_client)
    registry.load_all()
    return Translator(registry)


@pytest.fixture(scope="module")
def hybrid_executor(redis_client: redis.Redis, items_data: str) -> Executor:
    """Executor against the items index; skips when FT.HYBRID is unavailable."""
    if not _hybrid_supported(redis_client):
        pytest.skip("FT.HYBRID requires Redis 8.4+ (the test container is 8.0.2)")
    registry = SchemaRegistry(redis_client)
    registry.load_all()
    return Executor(redis_client, registry)


# A canonical hybrid query reused across layers.
HYBRID_SQL = (
    "SELECT name, description, "
    "hybrid_vector_search("
    "cosine_distance(embedding, :vec), "
    "fulltext(description, 'smartphone features'), "
    "rrf()"
    ") AS hybrid_score "
    "FROM items "
    "WHERE category = 'electronics' "
    "ORDER BY hybrid_score DESC "
    "LIMIT 5"
)


class TestHybridParserSelect:
    """Parsing hybrid_vector_search() out of the SELECT clause."""

    def test_detects_hybrid_search(self):
        """A hybrid_vector_search() projection populates ParsedQuery.hybrid_search."""
        parser = SQLParser()
        result = parser.parse(HYBRID_SQL)

        assert result.hybrid_search is not None
        assert result.index == "items"

    def test_extracts_vector_and_text_legs(self):
        """The vector and text legs are extracted from the nested functions."""
        parser = SQLParser()
        result = parser.parse(HYBRID_SQL)

        spec = result.hybrid_search
        assert spec.vector_field == "embedding"
        assert spec.text_field == "description"
        assert spec.text_query == "smartphone features"

    def test_combined_score_alias(self):
        """The SELECT alias becomes the combined-score column name."""
        parser = SQLParser()
        result = parser.parse(HYBRID_SQL)

        assert result.hybrid_search.alias == "hybrid_score"

    def test_defaults_rrf_and_bm25std(self):
        """rrf() with no scorer override yields RRF + the default BM25STD scorer."""
        parser = SQLParser()
        result = parser.parse(HYBRID_SQL)

        spec = result.hybrid_search
        assert spec.combine_method == "RRF"
        assert spec.text_scorer == "BM25STD"

    def test_defaults_to_knn_vector_method(self):
        """cosine_distance() selects the KNN vector method by default."""
        parser = SQLParser()
        result = parser.parse(HYBRID_SQL)

        assert result.hybrid_search.vector_method == "KNN"

    def test_where_condition_is_preserved(self):
        """The WHERE clause is retained as the per-leg filter."""
        parser = SQLParser()
        result = parser.parse(HYBRID_SQL)

        assert len(result.conditions) == 1
        assert result.conditions[0].field == "category"


class TestHybridParserKnobs:
    """Parsing the full set of fusion / leg knobs."""

    def test_linear_alpha(self):
        """linear(alpha => 0.3) selects LINEAR fusion with the given alpha."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT name, hybrid_vector_search("
            "cosine_distance(embedding, :vec), "
            "fulltext(description, 'phone'), "
            "linear(alpha => 0.3)"
            ") AS score FROM items LIMIT 5"
        )

        spec = result.hybrid_search
        assert spec.combine_method == "LINEAR"
        assert spec.linear_alpha == 0.3

    def test_rrf_constant_and_window(self):
        """rrf(constant => 60, window => 20) captures both RRF knobs."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT name, hybrid_vector_search("
            "cosine_distance(embedding, :vec), "
            "fulltext(description, 'phone'), "
            "rrf(constant => 60, window => 20)"
            ") AS score FROM items LIMIT 5"
        )

        spec = result.hybrid_search
        assert spec.rrf_constant == 60
        assert spec.rrf_window == 20

    def test_custom_text_scorer(self):
        """fulltext(..., scorer => 'TFIDF') overrides the default scorer."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT name, hybrid_vector_search("
            "cosine_distance(embedding, :vec), "
            "fulltext(description, 'phone', scorer => 'TFIDF'), "
            "rrf()"
            ") AS score FROM items LIMIT 5"
        )

        assert result.hybrid_search.text_scorer == "TFIDF"

    def test_knn_ef_runtime(self):
        """vector_distance(..., ef_runtime => 20) captures the KNN tuning knob.

        The tuning knob rides on vector_distance() rather than cosine_distance():
        sqlglot models cosine_distance as a built-in capped at 2 args, while
        vector_distance() parses as an anonymous function and accepts the extra arg.
        """
        parser = SQLParser()
        result = parser.parse(
            "SELECT name, hybrid_vector_search("
            "vector_distance(embedding, :vec, ef_runtime => 20), "
            "fulltext(description, 'phone'), "
            "rrf()"
            ") AS score FROM items LIMIT 5"
        )

        assert result.hybrid_search.ef_runtime == 20


class TestHybridParserValidation:
    """Error handling for malformed hybrid_vector_search() calls."""

    def test_missing_text_leg_raises(self):
        """hybrid_vector_search() without a text leg is rejected."""
        parser = SQLParser()
        with pytest.raises(ValueError):
            parser.parse(
                "SELECT name, hybrid_vector_search("
                "cosine_distance(embedding, :vec)"
                ") AS score FROM items LIMIT 5"
            )

    def test_combine_omitted_defaults_to_rrf(self):
        """A two-argument call (no combine) defaults to RRF fusion."""
        parser = SQLParser()
        result = parser.parse(
            "SELECT name, hybrid_vector_search("
            "cosine_distance(embedding, :vec), "
            "fulltext(description, 'phone')"
            ") AS score FROM items LIMIT 5"
        )

        assert result.hybrid_search.combine_method == "RRF"
        assert result.hybrid_search.rrf_constant is None

    def test_non_distance_vector_leg_raises(self):
        """A bare column as the vector leg is rejected."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="vector leg"):
            parser.parse(
                "SELECT name, hybrid_vector_search("
                "embedding, fulltext(description, 'phone'), rrf()"
                ") AS score FROM items LIMIT 5"
            )

    def test_unknown_vector_function_raises(self):
        """An unrecognized vector-leg function is rejected."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="vector leg"):
            parser.parse(
                "SELECT name, hybrid_vector_search("
                "made_up(embedding, :vec), fulltext(description, 'phone'), rrf()"
                ") AS score FROM items LIMIT 5"
            )

    def test_text_leg_not_fulltext_raises(self):
        """A non-fulltext() text leg is rejected."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="fulltext"):
            parser.parse(
                "SELECT name, hybrid_vector_search("
                "cosine_distance(embedding, :vec), made_up(description, 'p'), rrf()"
                ") AS score FROM items LIMIT 5"
            )

    def test_text_leg_non_string_query_raises(self):
        """A non-string fulltext() query is rejected."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="string literal"):
            parser.parse(
                "SELECT name, hybrid_vector_search("
                "cosine_distance(embedding, :vec), fulltext(description, 123), rrf()"
                ") AS score FROM items LIMIT 5"
            )

    def test_unknown_combine_method_raises(self):
        """An unrecognized fusion function is rejected."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="rrf|linear"):
            parser.parse(
                "SELECT name, hybrid_vector_search("
                "cosine_distance(embedding, :vec), fulltext(description, 'p'), foo()"
                ") AS score FROM items LIMIT 5"
            )

    def test_cosine_distance_non_column_field_raises(self):
        """A literal (not a column) as the cosine_distance field is rejected."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="column name"):
            parser.parse(
                "SELECT name, hybrid_vector_search("
                "cosine_distance('lit', :vec), fulltext(description, 'p'), rrf()"
                ") AS score FROM items LIMIT 5"
            )

    def test_vector_distance_non_column_field_raises(self):
        """A literal (not a column) as the vector_distance field is rejected."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="column name"):
            parser.parse(
                "SELECT name, hybrid_vector_search("
                "vector_distance(123, :vec), fulltext(description, 'p'), rrf()"
                ") AS score FROM items LIMIT 5"
            )

    def test_fulltext_insufficient_args_raises(self):
        """fulltext() with only a field (no query) is rejected."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="field and a"):
            parser.parse(
                "SELECT name, hybrid_vector_search("
                "cosine_distance(embedding, :vec), fulltext(description), rrf()"
                ") AS score FROM items LIMIT 5"
            )

    def test_fulltext_non_column_field_raises(self):
        """A literal (not a column) as the fulltext field is rejected."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="column name"):
            parser.parse(
                "SELECT name, hybrid_vector_search("
                "cosine_distance(embedding, :vec), fulltext(123, 'p'), rrf()"
                ") AS score FROM items LIMIT 5"
            )

    def test_vector_range_without_radius_raises(self):
        """vector_range() without a radius is rejected."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="radius"):
            parser.parse(
                "SELECT name, hybrid_vector_search("
                "vector_range(embedding, :vec), fulltext(description, 'p'), rrf()"
                ") AS score FROM items LIMIT 5"
            )

    def test_non_function_combine_raises(self):
        """A literal (not rrf/linear) as the fusion argument is rejected."""
        parser = SQLParser()
        with pytest.raises(ValueError, match="rrf|linear"):
            parser.parse(
                "SELECT name, hybrid_vector_search("
                "cosine_distance(embedding, :vec), fulltext(description, 'p'), 99"
                ") AS score FROM items LIMIT 5"
            )


class TestHybridAnalyzer:
    """Analyzing hybrid_vector_search() against a schema."""

    def test_detects_hybrid_search(self, sample_schema):
        """Analyzer surfaces the hybrid search on the analyzed query."""
        parser = SQLParser()
        parsed = parser.parse(HYBRID_SQL)
        result = Analyzer(sample_schema).analyze(parsed)

        assert result.hybrid_search is not None

    def test_resolves_leg_field_types(self, sample_schema):
        """Both leg fields resolve to their schema types."""
        parser = SQLParser()
        parsed = parser.parse(HYBRID_SQL)
        result = Analyzer(sample_schema).analyze(parsed)

        assert result.get_field_type("embedding") == "VECTOR"
        assert result.get_field_type("description") == "TEXT"

    def test_knn_k_derived_from_limit(self, sample_schema):
        """LIMIT becomes the KNN K for the vector leg."""
        parser = SQLParser()
        parsed = parser.parse(HYBRID_SQL)
        result = Analyzer(sample_schema).analyze(parsed)

        assert result.hybrid_search.k == 5

    def test_vector_leg_on_non_vector_field_raises(self, sample_schema):
        """Using a non-VECTOR field as the vector leg is an error."""
        parser = SQLParser()
        parsed = parser.parse(
            "SELECT name, hybrid_vector_search("
            "cosine_distance(price, :vec), "
            "fulltext(description, 'phone'), "
            "rrf()"
            ") AS score FROM items LIMIT 5"
        )
        with pytest.raises(ValueError):
            Analyzer(sample_schema).analyze(parsed)

    def test_text_leg_on_non_text_field_raises(self, sample_schema):
        """Using a non-TEXT field as the text leg is an error."""
        parser = SQLParser()
        parsed = parser.parse(
            "SELECT name, hybrid_vector_search("
            "cosine_distance(embedding, :vec), "
            "fulltext(price, 'phone'), "
            "rrf()"
            ") AS score FROM items LIMIT 5"
        )
        with pytest.raises(ValueError):
            Analyzer(sample_schema).analyze(parsed)


class TestHybridTranslator:
    """Translating hybrid_vector_search() to an FT.HYBRID command."""

    def test_emits_ft_hybrid_command(
        self, hybrid_translator: Translator, items_index: str
    ):
        """The translated command targets FT.HYBRID, not FT.SEARCH/FT.AGGREGATE."""
        result = hybrid_translator.translate(HYBRID_SQL)

        assert result.command == "FT.HYBRID"

    def test_command_has_search_and_vsim_legs(
        self, hybrid_translator: Translator, items_index: str
    ):
        """Both the SEARCH and VSIM legs appear in the rendered command."""
        cmd = hybrid_translator.translate(HYBRID_SQL).to_command_string()

        assert "SEARCH" in cmd
        assert "VSIM" in cmd
        assert "@embedding" in cmd

    def test_command_has_rrf_combine(
        self, hybrid_translator: Translator, items_index: str
    ):
        """Default fusion renders a COMBINE RRF clause."""
        cmd = hybrid_translator.translate(HYBRID_SQL).to_command_string()

        assert "COMBINE" in cmd
        assert "RRF" in cmd

    def test_command_omits_dialect(
        self, hybrid_translator: Translator, items_index: str
    ):
        """FT.HYBRID rejects an explicit DIALECT argument, so none is emitted."""
        result = hybrid_translator.translate(HYBRID_SQL)

        assert "DIALECT" not in result.to_command_string()

    def test_where_becomes_filter(
        self, hybrid_translator: Translator, items_index: str
    ):
        """The WHERE clause is rendered as a category filter on the legs."""
        cmd = hybrid_translator.translate(HYBRID_SQL).to_command_string()

        assert "electronics" in cmd

    def test_linear_combine_renders_alpha(
        self, hybrid_translator: Translator, items_index: str
    ):
        """A linear() fusion renders COMBINE LINEAR with ALPHA."""
        cmd = hybrid_translator.translate(
            "SELECT name, hybrid_vector_search("
            "cosine_distance(embedding, :vec), "
            "fulltext(description, 'smartphone'), "
            "linear(alpha => 0.3)"
            f") AS score FROM {items_index} LIMIT 5"
        ).to_command_string()

        assert "LINEAR" in cmd
        assert "ALPHA" in cmd

    def test_linear_derives_beta_from_alpha(
        self, hybrid_translator: Translator, items_index: str
    ):
        """LINEAR exposes alpha only; beta is derived as (1 - alpha)."""
        cmd = hybrid_translator.translate(
            "SELECT name, hybrid_vector_search("
            "cosine_distance(embedding, :vec), "
            "fulltext(description, 'smartphone'), "
            "linear(alpha => 0.3)"
            f") AS score FROM {items_index} LIMIT 5"
        ).to_command_string()

        assert "ALPHA 0.3" in cmd
        assert "BETA 0.7" in cmd

    def test_rrf_constant_and_window_in_command(
        self, hybrid_translator: Translator, items_index: str
    ):
        """RRF knobs render as CONSTANT and WINDOW."""
        cmd = hybrid_translator.translate(
            "SELECT name, hybrid_vector_search("
            "cosine_distance(embedding, :vec), "
            "fulltext(description, 'smartphone'), "
            "rrf(constant => 60, window => 20)"
            f") AS score FROM {items_index} LIMIT 5"
        ).to_command_string()

        assert "CONSTANT 60" in cmd
        assert "WINDOW 20" in cmd

    def test_knn_ef_runtime_in_command(
        self, hybrid_translator: Translator, items_index: str
    ):
        """A KNN ef_runtime knob renders EF_RUNTIME in the VSIM leg."""
        cmd = hybrid_translator.translate(
            "SELECT name, hybrid_vector_search("
            "vector_distance(embedding, :vec, ef_runtime => 20), "
            "fulltext(description, 'smartphone'), "
            "rrf()"
            f") AS score FROM {items_index} LIMIT 5"
        ).to_command_string()

        assert "EF_RUNTIME 20" in cmd

    def test_range_method_renders_radius(
        self, hybrid_translator: Translator, items_index: str
    ):
        """A vector_range() leg renders a RANGE method with RADIUS/EPSILON."""
        cmd = hybrid_translator.translate(
            "SELECT name, hybrid_vector_search("
            "vector_range(embedding, :vec, radius => 0.2, epsilon => 0.01), "
            "fulltext(description, 'smartphone'), "
            "rrf()"
            f") AS score FROM {items_index} LIMIT 5"
        ).to_command_string()

        assert "RANGE" in cmd
        assert "RADIUS 0.2" in cmd
        assert "EPSILON 0.01" in cmd

    def test_linear_window_in_command(
        self, hybrid_translator: Translator, items_index: str
    ):
        """A linear() window knob renders WINDOW in the COMBINE clause."""
        cmd = hybrid_translator.translate(
            "SELECT name, hybrid_vector_search("
            "cosine_distance(embedding, :vec), "
            "fulltext(description, 'smartphone'), "
            "linear(alpha => 0.3, window => 30)"
            f") AS score FROM {items_index} LIMIT 5"
        ).to_command_string()

        assert "WINDOW 30" in cmd

    def test_range_without_epsilon(
        self, hybrid_translator: Translator, items_index: str
    ):
        """vector_range() without epsilon renders RADIUS and no EPSILON."""
        cmd = hybrid_translator.translate(
            "SELECT name, hybrid_vector_search("
            "vector_range(embedding, :vec, radius => 0.2), "
            "fulltext(description, 'smartphone'), "
            "rrf()"
            f") AS score FROM {items_index} LIMIT 5"
        ).to_command_string()

        assert "RADIUS 0.2" in cmd
        assert "EPSILON" not in cmd

    def test_score_only_select_omits_load(
        self, hybrid_translator: Translator, items_index: str
    ):
        """With only the fused score projected, no LOAD clause is emitted."""
        cmd = hybrid_translator.translate(
            "SELECT hybrid_vector_search("
            "cosine_distance(embedding, :vec), "
            "fulltext(description, 'smartphone'), "
            "rrf()"
            f") AS score FROM {items_index} LIMIT 5"
        ).to_command_string()

        assert "LOAD" not in cmd

    def test_no_where_omits_vsim_filter(
        self, hybrid_translator: Translator, items_index: str
    ):
        """With no WHERE clause, the VSIM leg carries no FILTER."""
        cmd = hybrid_translator.translate(
            "SELECT name, hybrid_vector_search("
            "cosine_distance(embedding, :vec), "
            "fulltext(description, 'smartphone'), "
            "rrf()"
            f") AS score FROM {items_index} LIMIT 5"
        ).to_command_string()

        assert "FILTER" not in cmd


class _FakeRegistry:
    """Minimal schema registry returning the items schema for translation."""

    def get_schema(self, index: str) -> dict[str, str]:
        return {
            "name": "TEXT",
            "category": "TAG",
            "description": "TEXT",
            "embedding": "VECTOR",
        }


class _FakeClient:
    """Sync client stub that returns a canned reply for execute_command."""

    def __init__(self, reply):
        self.reply = reply
        self.last_command: tuple | None = None

    def execute_command(self, *args):
        self.last_command = args
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


_HYBRID_SQL_NO_WHERE = (
    "SELECT name, description, "
    "hybrid_vector_search("
    "cosine_distance(embedding, :vec), "
    "fulltext(description, 'smartphone'), "
    "rrf()"
    ") AS hybrid_score "
    "FROM items LIMIT 5"
)


class TestHybridExecutorVersionGuard:
    """The executor raises a clear error when FT.HYBRID is unsupported."""

    def test_unknown_command_raises_version_hint(self):
        """An 'unknown command' reply is rewrapped with the 8.4 requirement."""
        client = _FakeClient(redis.ResponseError("ERR unknown command 'FT.HYBRID'"))
        executor = Executor(client, _FakeRegistry())

        with pytest.raises(redis.ResponseError, match="8.4"):
            executor.execute(_HYBRID_SQL_NO_WHERE, params={"vec": b"\x00" * 16})


class TestHybridExecutorParsing:
    """The executor parses FT.HYBRID replies into rows with the fused score."""

    def test_parses_rows_with_combined_score(self):
        """A hybrid reply maps field/value pairs (incl. the score) into rows."""
        reply = [
            "total_results",
            1,
            "results",
            [["name", "iPhone 15", "description", "smartphone", "hybrid_score", "0.5"]],
            "warnings",
            [],
            "execution_time",
            "0.1",
        ]
        client = _FakeClient(reply)
        executor = Executor(client, _FakeRegistry())

        result = executor.execute(_HYBRID_SQL_NO_WHERE, params={"vec": b"\x00" * 16})

        assert result.count == 1
        assert result.rows[0]["name"] == "iPhone 15"
        assert result.rows[0]["hybrid_score"] == "0.5"

    def test_parses_rows_from_resp3_dict_reply(self):
        """A redis-py 8.x / RESP3 map reply (dict of dict rows) parses to rows."""
        reply = {
            b"total_results": 1,
            b"results": [{b"name": b"iPhone 15", b"hybrid_score": b"0.5"}],
            b"warnings": [],
            b"execution_time": 0.1,
        }
        client = _FakeClient(reply)
        executor = Executor(client, _FakeRegistry())

        result = executor.execute(_HYBRID_SQL_NO_WHERE, params={"vec": b"\x00" * 16})

        assert result.count == 1
        assert result.rows[0][b"name"] == b"iPhone 15"
        assert result.rows[0][b"hybrid_score"] == b"0.5"

    def test_vector_bytes_injected_into_command(self):
        """The vector param bytes replace the $vector placeholder in the command."""
        client = _FakeClient([0])
        executor = Executor(client, _FakeRegistry())
        blob = b"\x01" * 16

        executor.execute(_HYBRID_SQL_NO_WHERE, params={"vec": blob})

        assert client.last_command[0] == "FT.HYBRID"
        # The bytes are injected as the PARAMS value...
        assert blob in client.last_command
        # ...while the VSIM leg keeps the $vector parameter reference.
        assert "$vector" in client.last_command


class _FakeAsyncRegistry:
    """Minimal async schema registry for the async executor unit tests."""

    async def ensure_schema(self, index: str) -> None:
        return None

    def get_schema(self, index: str) -> dict[str, str]:
        return {
            "name": "TEXT",
            "category": "TAG",
            "description": "TEXT",
            "embedding": "VECTOR",
        }


class _FakeAsyncClient:
    """Async client stub that returns a canned reply for execute_command."""

    def __init__(self, reply):
        self.reply = reply
        self.last_command: tuple | None = None

    async def execute_command(self, *args):
        self.last_command = args
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class TestHybridAsyncExecutor:
    """Async executor mirrors the sync FT.HYBRID guard and parsing."""

    async def test_async_version_guard(self):
        """An 'unknown command' reply is rewrapped with the 8.4 requirement."""
        from sql_redis.executor import AsyncExecutor

        client = _FakeAsyncClient(
            redis.ResponseError("ERR unknown command 'FT.HYBRID'")
        )
        executor = AsyncExecutor(client, _FakeAsyncRegistry())

        with pytest.raises(redis.ResponseError, match="8.4"):
            await executor.execute(_HYBRID_SQL_NO_WHERE, params={"vec": b"\x00" * 16})

    async def test_async_parses_rows(self):
        """An async hybrid reply parses into rows with the fused score."""
        from sql_redis.executor import AsyncExecutor

        reply = [
            "total_results",
            1,
            "results",
            [["name", "iPhone 15", "hybrid_score", "0.5"]],
            "warnings",
            [],
        ]
        client = _FakeAsyncClient(reply)
        executor = AsyncExecutor(client, _FakeAsyncRegistry())

        result = await executor.execute(
            _HYBRID_SQL_NO_WHERE, params={"vec": b"\x00" * 16}
        )

        assert result.rows[0]["name"] == "iPhone 15"
        assert result.rows[0]["hybrid_score"] == "0.5"


class TestHybridFusionIntegration:
    """End-to-end FT.HYBRID execution (requires Redis 8.4+)."""

    def test_returns_fused_rows(self, hybrid_executor: Executor, items_data: str):
        """A hybrid fusion query returns rows with the combined-score column."""
        query_vector = float_vector_to_bytes([0.1, 0.2, 0.3, 0.4])

        result = hybrid_executor.execute(
            f"""
            SELECT name, description,
                   hybrid_vector_search(
                       cosine_distance(embedding, :vec),
                       fulltext(description, 'smartphone features'),
                       rrf()
                   ) AS hybrid_score
            FROM {items_data}
            WHERE category = 'electronics'
            ORDER BY hybrid_score DESC
            LIMIT 5
            """,
            params={"vec": query_vector},
        )

        assert len(result.rows) >= 1
        assert "hybrid_score" in result.rows[0]

    def test_linear_fusion_executes(self, hybrid_executor: Executor, items_data: str):
        """LINEAR fusion with an alpha weight executes end-to-end."""
        query_vector = float_vector_to_bytes([0.1, 0.2, 0.3, 0.4])

        result = hybrid_executor.execute(
            f"""
            SELECT name,
                   hybrid_vector_search(
                       cosine_distance(embedding, :vec),
                       fulltext(description, 'smartphone'),
                       linear(alpha => 0.3)
                   ) AS hybrid_score
            FROM {items_data}
            LIMIT 5
            """,
            params={"vec": query_vector},
        )

        assert len(result.rows) >= 1
