"""Unit coverage for explicit filtered-vector execution policy translation."""

from typing import cast

import pytest
import redis
import redis.asyncio as async_redis

from sql_redis.executor import create_async_executor, create_executor
from sql_redis.schema import AsyncSchemaRegistry, SchemaRegistry
from sql_redis.translator import Translator


class _StaticSchemaRegistry:
    def get_schema(self, index: str) -> dict[str, str]:
        if index != "vectors":
            return {}
        return {
            "title": "TEXT",
            "category": "TAG",
            "embedding": "VECTOR",
        }


FILTERED_SQL = (
    "SELECT title, vector_distance(embedding, :vec) AS score "
    "FROM vectors WHERE category = 'books' LIMIT 5"
)
UNFILTERED_SQL = (
    "SELECT title, vector_distance(embedding, :vec) AS score " "FROM vectors LIMIT 5"
)


@pytest.mark.parametrize("policy", ["BATCHES", "ADHOC_BF"])
def test_filtered_knn_emits_hybrid_policy(policy: str):
    translator = Translator(_StaticSchemaRegistry(), hybrid_policy=policy)  # type: ignore[arg-type]

    result = translator.translate(FILTERED_SQL)

    assert f"HYBRID_POLICY {policy}" in result.query_string


def test_hybrid_policy_is_normalized_to_uppercase():
    translator = Translator(  # type: ignore[arg-type]
        _StaticSchemaRegistry(), hybrid_policy="batches"
    )

    assert "HYBRID_POLICY BATCHES" in translator.translate(FILTERED_SQL).query_string


def test_hybrid_policy_does_not_change_unfiltered_knn():
    translator = Translator(  # type: ignore[arg-type]
        _StaticSchemaRegistry(), hybrid_policy="BATCHES"
    )

    assert "HYBRID_POLICY" not in translator.translate(UNFILTERED_SQL).query_string


def test_invalid_hybrid_policy_is_rejected():
    with pytest.raises(ValueError, match="hybrid_policy must be one of"):
        Translator(_StaticSchemaRegistry(), hybrid_policy="invalid")  # type: ignore[arg-type]


def test_sync_executor_factory_passes_hybrid_policy_to_translator():
    registry = cast(SchemaRegistry, _StaticSchemaRegistry())
    executor = create_executor(
        cast(redis.Redis, object()),
        schema_registry=registry,
        hybrid_policy="ADHOC_BF",
    )

    assert (
        "HYBRID_POLICY ADHOC_BF"
        in executor._translator.translate(FILTERED_SQL).query_string
    )


@pytest.mark.asyncio
async def test_async_executor_factory_passes_hybrid_policy_to_translator():
    registry = cast(AsyncSchemaRegistry, _StaticSchemaRegistry())
    executor = await create_async_executor(
        cast(async_redis.Redis, object()),
        schema_registry=registry,
        hybrid_policy="BATCHES",
    )

    assert (
        "HYBRID_POLICY BATCHES"
        in executor._translator.translate(FILTERED_SQL).query_string
    )


HYBRID_SQL = (
    "SELECT title, hybrid_vector_search("
    "cosine_distance(embedding, :vec), fulltext(title, 'redis'), rrf()"
    ") AS score FROM vectors WHERE category = 'books' LIMIT 5"
)


@pytest.mark.parametrize(
    "policy, expected", [("ADHOC_BF", "ADHOC"), ("batches", "BATCHES")]
)
@pytest.mark.parametrize("fusion", ["rrf()", "linear(alpha => 0.3)"])
@pytest.mark.parametrize(
    "vector",
    [
        "cosine_distance(embedding, :vec)",
        "vector_range(embedding, :vec, radius => 0.2)",
    ],
)
def test_filtered_hybrid_policy_preserves_query_semantics(
    policy, expected, fusion, vector
):
    sql = HYBRID_SQL.replace("rrf()", fusion).replace(
        "cosine_distance(embedding, :vec)", vector
    )
    baseline = Translator(_StaticSchemaRegistry()).translate(sql).to_command_list()
    command = (
        Translator(_StaticSchemaRegistry(), hybrid_policy=policy)
        .translate(sql)
        .to_command_list()
    )
    position = command.index("FILTER")
    assert command[position : position + 5] == [
        "FILTER",
        "3",
        "@category:{books}",
        "POLICY",
        expected,
    ]
    # Only the policy and FILTER argument count may change.
    command[position + 1] = "1"
    del command[position + 3 : position + 5]
    assert command == baseline


@pytest.mark.parametrize("policy", ["ADHOC_BF", "BATCHES"])
def test_unfiltered_hybrid_unchanged_with_policy(policy):
    sql = HYBRID_SQL.replace(" WHERE category = 'books'", "")
    baseline = Translator(_StaticSchemaRegistry()).translate(sql).to_command_list()
    command = (
        Translator(_StaticSchemaRegistry(), hybrid_policy=policy)
        .translate(sql)
        .to_command_list()
    )
    assert command == baseline
    assert "FILTER" not in command


def test_filtered_hybrid_without_policy_preserves_default():
    command = (
        Translator(_StaticSchemaRegistry()).translate(HYBRID_SQL).to_command_list()
    )
    position = command.index("FILTER")
    assert command[position : position + 3] == ["FILTER", "1", "@category:{books}"]
    assert "POLICY" not in command


@pytest.mark.parametrize(
    "policy, expected", [("ADHOC_BF", "ADHOC"), ("BATCHES", "BATCHES")]
)
@pytest.mark.asyncio
async def test_executor_factories_forward_hybrid_filter_policy(policy, expected):
    registry = _StaticSchemaRegistry()
    sync_executor = create_executor(
        cast(redis.Redis, object()), schema_registry=registry, hybrid_policy=policy
    )
    async_executor = await create_async_executor(
        cast(async_redis.Redis, object()),
        schema_registry=registry,
        hybrid_policy=policy,
    )
    for executor in [sync_executor, async_executor]:
        command = executor._translator.translate(HYBRID_SQL).to_command_list()
        position = command.index("FILTER")
        assert command[position : position + 5] == [
            "FILTER",
            "3",
            "@category:{books}",
            "POLICY",
            expected,
        ]
