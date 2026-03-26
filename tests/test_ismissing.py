"""Integration tests for IS NULL / IS NOT NULL → ismissing() feature.

Tests run against a real Redis 8 instance to verify end-to-end behavior:
- Index creation with INDEXMISSING attribute
- Documents with and without optional fields
- SQL IS NULL / IS NOT NULL queries producing correct results
- Combined conditions (IS NULL with other filters)
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
def missing_index(redis_client: redis.Redis) -> str:
    """Create an index with INDEXMISSING on optional fields.

    Schema:
        name     TEXT SORTABLE       (required — present on all docs)
        category TAG  SORTABLE       (required — present on all docs)
        email    TAG  INDEXMISSING   (optional — absent on some docs)
        bio      TEXT INDEXMISSING   (optional — absent on some docs)
        score    NUMERIC INDEXMISSING (optional — absent on some docs)
    """
    index_name = "test_ismissing"
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
        "missing:",
        "SCHEMA",
        "name",
        "TEXT",
        "SORTABLE",
        "category",
        "TAG",
        "SORTABLE",
        "email",
        "TAG",
        "INDEXMISSING",
        "bio",
        "TEXT",
        "INDEXMISSING",
        "score",
        "NUMERIC",
        "INDEXMISSING",
    )
    return index_name


@pytest.fixture
def missing_data(redis_client: redis.Redis, missing_index: str) -> str:
    """Populate the index with documents — some with optional fields, some without.

    Documents:
        missing:1  name=Alice   category=eng   email=alice@co  bio=Engineer    score=95
        missing:2  name=Bob     category=eng   (no email)      (no bio)        score=80
        missing:3  name=Carol   category=sales email=carol@co  (no bio)        (no score)
        missing:4  name=Dave    category=sales (no email)      (no bio)        (no score)
    """
    redis_client.hset(
        "missing:1",
        mapping={
            "name": "Alice",
            "category": "eng",
            "email": "alice@co",
            "bio": "Engineer at Acme",
            "score": "95",
        },
    )
    redis_client.hset(
        "missing:2",
        mapping={
            "name": "Bob",
            "category": "eng",
            "score": "80",
        },
    )
    redis_client.hset(
        "missing:3",
        mapping={
            "name": "Carol",
            "category": "sales",
            "email": "carol@co",
        },
    )
    redis_client.hset(
        "missing:4",
        mapping={
            "name": "Dave",
            "category": "sales",
        },
    )
    return missing_index


@pytest.fixture
def missing_executor(redis_client: redis.Redis, missing_data: str) -> Executor:
    """Create an executor with the ismissing index loaded."""
    registry = SchemaRegistry(redis_client)
    registry.refresh(missing_data)
    return Executor(redis_client, registry)


@pytest.fixture
def missing_translator(redis_client: redis.Redis, missing_data: str) -> Translator:
    """Create a translator with the ismissing index loaded."""
    registry = SchemaRegistry(redis_client)
    registry.refresh(missing_data)
    return Translator(registry)


# ---------------------------------------------------------------------------
# Translation tests — verify generated Redis commands
# ---------------------------------------------------------------------------


class TestIsMissingTranslation:
    """Verify SQL IS NULL / IS NOT NULL translates to correct Redis commands."""

    def test_is_null_query_string(self, missing_translator, missing_data):
        """IS NULL → ismissing(@field) in query string."""
        result = missing_translator.translate(
            f"SELECT * FROM {missing_data} WHERE email IS NULL"
        )
        assert result.command == "FT.SEARCH"
        assert result.query_string == "ismissing(@email)"
        assert result.args[-2:] == ["DIALECT", "2"]

    def test_is_null_numeric_field(self, missing_translator, missing_data):
        """IS NULL works on NUMERIC fields with INDEXMISSING."""
        result = missing_translator.translate(
            f"SELECT * FROM {missing_data} WHERE score IS NULL"
        )
        assert result.query_string == "ismissing(@score)"

    def test_is_null_combined_with_tag(self, missing_translator, missing_data):
        """IS NULL combined with TAG filter."""
        result = missing_translator.translate(
            f"SELECT * FROM {missing_data} WHERE category = 'eng' AND email IS NULL"
        )
        assert "ismissing(@email)" in result.query_string
        assert "@category:{eng}" in result.query_string

    def test_is_not_null_combined_with_tag(self, missing_translator, missing_data):
        """IS NOT NULL combined with TAG filter."""
        result = missing_translator.translate(
            f"SELECT * FROM {missing_data} WHERE category = 'sales' AND email IS NOT NULL"
        )
        assert "-ismissing(@email)" in result.query_string
        assert "@category:{sales}" in result.query_string


# ---------------------------------------------------------------------------
# Execution tests — verify actual Redis results
# ---------------------------------------------------------------------------


class TestIsMissingExecution:
    """Execute IS NULL / IS NOT NULL queries against Redis and verify results."""

    def test_is_null_tag_field(self, missing_executor, missing_data):
        """IS NULL on TAG field returns docs where field is absent.

        email is absent on: Bob (missing:2), Dave (missing:4)
        """
        result = missing_executor.execute(
            f"SELECT * FROM {missing_data} WHERE email IS NULL"
        )
        assert result.count == 2
        names = {row["name"] for row in result.rows}
        assert names == {"Bob", "Dave"}

    def test_is_not_null_tag_field(self, missing_executor, missing_data):
        """IS NOT NULL on TAG field returns docs where field is present.

        email is present on: Alice (missing:1), Carol (missing:3)
        """
        result = missing_executor.execute(
            f"SELECT * FROM {missing_data} WHERE email IS NOT NULL"
        )
        assert result.count == 2
        names = {row["name"] for row in result.rows}
        assert names == {"Alice", "Carol"}

    def test_is_null_text_field(self, missing_executor, missing_data):
        """IS NULL on TEXT field returns docs where field is absent.

        bio is absent on: Bob (missing:2), Carol (missing:3), Dave (missing:4)
        """
        result = missing_executor.execute(
            f"SELECT * FROM {missing_data} WHERE bio IS NULL"
        )
        assert result.count == 3
        names = {row["name"] for row in result.rows}
        assert names == {"Bob", "Carol", "Dave"}

    def test_is_not_null_text_field(self, missing_executor, missing_data):
        """IS NOT NULL on TEXT field returns docs where field is present.

        bio is present on: Alice (missing:1) only
        """
        result = missing_executor.execute(
            f"SELECT * FROM {missing_data} WHERE bio IS NOT NULL"
        )
        assert result.count == 1
        assert result.rows[0]["name"] == "Alice"

    def test_is_null_numeric_field(self, missing_executor, missing_data):
        """IS NULL on NUMERIC field returns docs where field is absent.

        score is absent on: Carol (missing:3), Dave (missing:4)
        """
        result = missing_executor.execute(
            f"SELECT * FROM {missing_data} WHERE score IS NULL"
        )
        assert result.count == 2
        names = {row["name"] for row in result.rows}
        assert names == {"Carol", "Dave"}

    def test_is_not_null_numeric_field(self, missing_executor, missing_data):
        """IS NOT NULL on NUMERIC field returns docs where field is present.

        score is present on: Alice (missing:1, score=95), Bob (missing:2, score=80)
        """
        result = missing_executor.execute(
            f"SELECT * FROM {missing_data} WHERE score IS NOT NULL"
        )
        assert result.count == 2
        names = {row["name"] for row in result.rows}
        assert names == {"Alice", "Bob"}


class TestIsMissingCombined:
    """Test IS NULL / IS NOT NULL combined with other conditions."""

    def test_is_null_and_tag_filter(self, missing_executor, missing_data):
        """IS NULL AND tag = value narrows results.

        email IS NULL: Bob, Dave
        category = 'eng': Alice, Bob
        Intersection: Bob only
        """
        result = missing_executor.execute(
            f"SELECT * FROM {missing_data} WHERE category = 'eng' AND email IS NULL"
        )
        assert result.count == 1
        assert result.rows[0]["name"] == "Bob"

    def test_is_not_null_and_tag_filter(self, missing_executor, missing_data):
        """IS NOT NULL AND tag = value narrows results.

        email IS NOT NULL: Alice, Carol
        category = 'sales': Carol, Dave
        Intersection: Carol only
        """
        result = missing_executor.execute(
            f"SELECT * FROM {missing_data} WHERE category = 'sales' AND email IS NOT NULL"
        )
        assert result.count == 1
        assert result.rows[0]["name"] == "Carol"

    def test_multiple_is_null(self, missing_executor, missing_data):
        """Multiple IS NULL conditions combined with AND.

        email IS NULL: Bob, Dave
        bio IS NULL: Bob, Carol, Dave
        Intersection: Bob, Dave
        """
        result = missing_executor.execute(
            f"SELECT * FROM {missing_data} WHERE email IS NULL AND bio IS NULL"
        )
        assert result.count == 2
        names = {row["name"] for row in result.rows}
        assert names == {"Bob", "Dave"}

    def test_is_null_and_is_not_null_different_fields(
        self, missing_executor, missing_data
    ):
        """IS NULL on one field AND IS NOT NULL on another.

        email IS NULL: Bob, Dave
        score IS NOT NULL: Alice, Bob
        Intersection: Bob only
        """
        result = missing_executor.execute(
            f"SELECT * FROM {missing_data} WHERE email IS NULL AND score IS NOT NULL"
        )
        assert result.count == 1
        assert result.rows[0]["name"] == "Bob"


class TestIsMissingEdgeCases:
    """Edge cases for ismissing() behavior."""

    def test_is_null_returns_all_when_all_missing(self, missing_executor, missing_data):
        """IS NULL on a field missing from most docs returns the correct count.

        bio is only present on Alice. IS NULL should return Bob, Carol, Dave.
        """
        result = missing_executor.execute(
            f"SELECT * FROM {missing_data} WHERE bio IS NULL"
        )
        assert result.count == 3

    def test_is_not_null_on_indexmissing_field_returns_present_docs(
        self, redis_client, missing_data
    ):
        """IS NOT NULL on an INDEXMISSING optional field returns only present docs.

        email is defined with INDEXMISSING and is present on Alice and Carol
        (2 of the 4 docs). Querying `email IS NOT NULL` should return exactly
        those two documents. (We cannot use `name` here because it does not
        have INDEXMISSING configured.)
        """
        # email IS NOT NULL: Alice, Carol (2 docs)
        registry = SchemaRegistry(redis_client)
        registry.refresh(missing_data)
        executor = Executor(redis_client, registry)
        result = executor.execute(
            f"SELECT * FROM {missing_data} WHERE email IS NOT NULL"
        )
        assert result.count == 2

    def test_raw_ismissing_command_works(self, redis_client, missing_data):
        """Verify the raw FT.SEARCH ismissing() command works with Redis."""
        result = redis_client.execute_command(
            "FT.SEARCH",
            "test_ismissing",
            "ismissing(@email)",
            "DIALECT",
            "2",
        )
        # Should return Bob and Dave (2 docs)
        assert result[0] == 2

    def test_raw_neg_ismissing_command_works(self, redis_client, missing_data):
        """Verify the raw FT.SEARCH -ismissing() command works with Redis."""
        result = redis_client.execute_command(
            "FT.SEARCH",
            "test_ismissing",
            "-ismissing(@email)",
            "DIALECT",
            "2",
        )
        # Should return Alice and Carol (2 docs)
        assert result[0] == 2



class TestIsMissingErrorHandling:
    """Test error messages when ismissing() is used on unsupported schemas."""

    def test_ismissing_without_indexmissing_gives_clear_error(
        self,
        redis_client,
    ):
        """Using IS NULL on a field without INDEXMISSING gives a clear error."""
        # Create an index WITHOUT INDEXMISSING on the email field
        idx = "test_no_indexmissing"
        try:
            redis_client.execute_command("FT.DROPINDEX", idx, "DD")
        except redis.ResponseError:
            pass

        redis_client.execute_command(
            "FT.CREATE",
            idx,
            "ON",
            "HASH",
            "PREFIX",
            "1",
            "noim:",
            "SCHEMA",
            "name",
            "TEXT",
            "SORTABLE",
            "email",
            "TAG",
        )
        redis_client.hset("noim:1", mapping={"name": "Alice"})

        registry = SchemaRegistry(redis_client)
        registry.refresh(idx)
        executor = Executor(redis_client, registry)

        with pytest.raises(redis.ResponseError, match="INDEXMISSING"):
            executor.execute(f"SELECT * FROM {idx} WHERE email IS NULL")

        # Cleanup
        try:
            redis_client.execute_command("FT.DROPINDEX", idx, "DD")
        except redis.ResponseError:
            pass

    def test_is_null_translation_emits_warning(self, missing_translator, missing_data):
        """IS NULL translation emits a warning about Redis version requirement."""
        import warnings

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            missing_translator.translate(
                f"SELECT * FROM {missing_data} WHERE email IS NULL"
            )
            assert len(w) == 1
            assert "Redis 7.4+" in str(w[0].message)
            assert "INDEXMISSING" in str(w[0].message)
