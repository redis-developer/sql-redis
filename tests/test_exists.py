"""Integration tests for exists() → FT.AGGREGATE APPLY/FILTER.

Tests run against a real Redis 8 instance to verify end-to-end behavior:
- exists(field) in SELECT → APPLY exists(@field) AS alias
- exists(field) in HAVING → FILTER exists(@field)
- LOAD includes fields referenced in exists()
- exists() in WHERE raises ValueError
"""

import pytest
import redis

from sql_redis.executor import Executor
from sql_redis.schema import SchemaRegistry
from sql_redis.translator import Translator

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def exists_index(redis_client: redis.Redis) -> str:
    """Create an index for exists() testing.

    Schema:
        name     TEXT SORTABLE   (required — present on all docs)
        category TAG  SORTABLE   (required — present on all docs)
        email    TAG             (optional — absent on some docs)
        score    NUMERIC         (optional — absent on some docs)
    """
    index_name = "test_exists"
    try:
        redis_client.execute_command("FT.DROPINDEX", index_name, "DD")
    except redis.ResponseError:
        pass

    redis_client.execute_command(
        "FT.CREATE",
        index_name,
        "ON",
        "HASH",
        "PREFIX",
        "1",
        "exists:",
        "SCHEMA",
        "name",
        "TEXT",
        "SORTABLE",
        "category",
        "TAG",
        "SORTABLE",
        "email",
        "TAG",
        "score",
        "NUMERIC",
    )
    return index_name


@pytest.fixture
def exists_data(redis_client: redis.Redis, exists_index: str) -> str:
    """Populate index with documents — some with optional fields, some without.

    Documents:
        exists:1  name=Alice   category=eng   email=alice@co  score=95
        exists:2  name=Bob     category=eng   (no email)      score=80
        exists:3  name=Carol   category=sales email=carol@co  (no score)
        exists:4  name=Dave    category=sales (no email)      (no score)
    """
    redis_client.hset(
        "exists:1",
        mapping={
            "name": "Alice",
            "category": "eng",
            "email": "alice@co",
            "score": "95",
        },
    )
    redis_client.hset(
        "exists:2",
        mapping={"name": "Bob", "category": "eng", "score": "80"},
    )
    redis_client.hset(
        "exists:3",
        mapping={"name": "Carol", "category": "sales", "email": "carol@co"},
    )
    redis_client.hset(
        "exists:4",
        mapping={"name": "Dave", "category": "sales"},
    )
    return exists_index


@pytest.fixture
def translator(redis_client: redis.Redis, exists_data: str) -> Translator:
    """Create a translator with the exists index loaded."""
    registry = SchemaRegistry(redis_client)
    registry.refresh(exists_data)
    return Translator(registry)


@pytest.fixture
def executor(redis_client: redis.Redis, exists_data: str) -> Executor:
    """Create an executor with the exists index loaded."""
    registry = SchemaRegistry(redis_client)
    registry.refresh(exists_data)
    return Executor(redis_client, registry)


# ---------------------------------------------------------------------------
# Tests: exists() in SELECT → APPLY
# ---------------------------------------------------------------------------


class TestExistsInSelect:
    """exists(field) in SELECT generates FT.AGGREGATE with APPLY."""

    def test_exists_email_projection(self, executor: Executor, exists_data: str):
        """exists(email) returns 1 for docs with email, 0 for docs without."""
        result = executor.execute(
            f"SELECT name, exists(email) AS has_email FROM {exists_data}"
        )
        by_name = {r["name"]: r for r in result.rows}
        assert by_name["Alice"]["has_email"] == "1"
        assert by_name["Bob"]["has_email"] == "0"
        assert by_name["Carol"]["has_email"] == "1"
        assert by_name["Dave"]["has_email"] == "0"

    def test_exists_score_projection(self, executor: Executor, exists_data: str):
        """exists(score) returns 1 for docs with score, 0 for docs without."""
        result = executor.execute(
            f"SELECT name, exists(score) AS has_score FROM {exists_data}"
        )
        by_name = {r["name"]: r for r in result.rows}
        assert by_name["Alice"]["has_score"] == "1"
        assert by_name["Bob"]["has_score"] == "1"
        assert by_name["Carol"]["has_score"] == "0"
        assert by_name["Dave"]["has_score"] == "0"

    def test_multiple_exists_projections(self, executor: Executor, exists_data: str):
        """Multiple exists() produce multiple APPLY clauses with correct values."""
        result = executor.execute(
            f"SELECT name, exists(email) AS has_email, exists(score) AS has_score "
            f"FROM {exists_data}"
        )
        by_name = {r["name"]: r for r in result.rows}
        # Alice has both
        assert by_name["Alice"]["has_email"] == "1"
        assert by_name["Alice"]["has_score"] == "1"
        # Dave has neither
        assert by_name["Dave"]["has_email"] == "0"
        assert by_name["Dave"]["has_score"] == "0"


# ---------------------------------------------------------------------------
# Tests: exists() in HAVING → FILTER
# ---------------------------------------------------------------------------


class TestExistsInHaving:
    """HAVING exists(field) generates FT.AGGREGATE with FILTER."""

    def test_having_exists_filters_results(self, executor: Executor, exists_data: str):
        """HAVING exists(email) only returns docs where email is present."""
        result = executor.execute(
            f"SELECT name, email FROM {exists_data} HAVING exists(email)"
        )
        names = sorted(r["name"] for r in result.rows)
        assert names == ["Alice", "Carol"]

    def test_having_exists_score(self, executor: Executor, exists_data: str):
        """HAVING exists(score) only returns docs where score is present."""
        result = executor.execute(
            f"SELECT name, score FROM {exists_data} HAVING exists(score)"
        )
        names = sorted(r["name"] for r in result.rows)
        assert names == ["Alice", "Bob"]


# ---------------------------------------------------------------------------
# Tests: Raw command verification
# ---------------------------------------------------------------------------


class TestExistsCommandStructure:
    """Verify generated Redis commands have correct structure."""

    def test_select_exists_command(self, translator: Translator, exists_data: str):
        """exists(field) in SELECT produces correct FT.AGGREGATE command."""
        result = translator.translate(
            f"SELECT exists(email) AS has_email FROM {exists_data}"
        )
        assert result.command == "FT.AGGREGATE"
        assert "APPLY" in result.args
        apply_idx = result.args.index("APPLY")
        assert "exists(@email)" in result.args[apply_idx + 1]
        assert result.args[apply_idx + 2] == "AS"
        assert result.args[apply_idx + 3] == "has_email"
        assert "LOAD" in result.args
        assert result.args[-2:] == ["DIALECT", "2"]

    def test_having_exists_command(self, translator: Translator, exists_data: str):
        """HAVING exists(field) produces FILTER in FT.AGGREGATE."""
        result = translator.translate(
            f"SELECT name FROM {exists_data} HAVING exists(email)"
        )
        assert result.command == "FT.AGGREGATE"
        assert "FILTER" in result.args
        filter_idx = result.args.index("FILTER")
        assert "exists(@email)" in result.args[filter_idx + 1]


# ---------------------------------------------------------------------------
# Tests: Error cases
# ---------------------------------------------------------------------------


class TestExistsErrors:
    """Verify error handling for invalid exists() usage."""

    def test_exists_in_where_raises(self, translator: Translator, exists_data: str):
        """exists() in WHERE raises ValueError."""
        with pytest.raises(ValueError, match="exists.*aggregate"):
            translator.translate(f"SELECT * FROM {exists_data} WHERE exists(email)")
