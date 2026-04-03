"""Benchmark SQLQuery vs raw FT.SEARCH for the user_simple example index.

Requires an existing ``user_simple`` index in Redis, such as the one created in
``docs/user_guide/12_sql_to_redis_queries.ipynb``.

Usage:
    uv run --extra sql-redis python benchmarks/bench_user_simple_query.py
    uv run --extra sql-redis python benchmarks/bench_user_simple_query.py --iterations 250
    uv run --extra sql-redis python benchmarks/bench_user_simple_query.py --output my_results.csv
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any, Callable

from redis import Redis
from redis.exceptions import ResponseError
from redisvl.index import SearchIndex
from redisvl.query import SQLQuery

SQL_STR = """
    SELECT user, region, job, age
    FROM user_simple
    WHERE age > 17
    """

FT_SEARCH_ARGS = (
    "FT.SEARCH",
    "user_simple",
    "@age:[(17 +inf]",
    "RETURN",
    "4",
    "user",
    "region",
    "job",
    "age",
    "DIALECT",
    "2",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--redis-url",
        default="redis://localhost:6379",
        help="Redis connection URL.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=100,
        help="Number of measured executions per mode.",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=3,
        help="Warmup executions to run before timing each mode.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmarks/user_simple_query_benchmark.csv"),
        help="Where to write the CSV results.",
    )
    return parser.parse_args()


def ensure_user_simple_exists(client: Redis) -> None:
    try:
        client.execute_command("FT.INFO", "user_simple")
    except ResponseError as exc:
        raise SystemExit(
            "Index 'user_simple' was not found. Run the setup cells in "
            "docs/user_guide/12_sql_to_redis_queries.ipynb first."
        ) from exc


def benchmark_mode(
    name: str,
    runner: Callable[[], Any],
    result_counter: Callable[[Any], int],
    iterations: int,
    warmup: int,
) -> dict[str, Any]:
    for _ in range(warmup):
        runner()

    durations_ms: list[float] = []
    rows_returned = 0
    total_start = time.perf_counter()

    for _ in range(iterations):
        start = time.perf_counter()
        result = runner()
        durations_ms.append((time.perf_counter() - start) * 1000)
        rows_returned = result_counter(result)

    total_ms = (time.perf_counter() - total_start) * 1000
    return {
        "mode": name,
        "iterations": iterations,
        "warmup": warmup,
        "avg_query_ms": sum(durations_ms) / len(durations_ms),
        "min_query_ms": min(durations_ms),
        "max_query_ms": max(durations_ms),
        "total_ms": total_ms,
        "rows_returned": rows_returned,
    }


def count_sql_rows(result: list[dict[str, Any]]) -> int:
    return len(result)


def count_ft_search_rows(result: Any) -> int:
    return int(result[0]) if result else 0


def main() -> None:
    args = parse_args()

    import pandas as pd

    client = Redis.from_url(args.redis_url)
    ensure_user_simple_exists(client)

    lazy_index = SearchIndex.from_existing("user_simple", redis_client=client)
    load_all_index = SearchIndex.from_existing("user_simple", redis_client=client)

    lazy_query = SQLQuery(
        SQL_STR,
        sql_redis_options={"schema_cache_strategy": "lazy"},
    )
    load_all_query = SQLQuery(
        SQL_STR,
        sql_redis_options={"schema_cache_strategy": "load_all"},
    )

    results = [
        benchmark_mode(
            name="sqlquery_lazy",
            runner=lambda: lazy_index.query(lazy_query),
            result_counter=count_sql_rows,
            iterations=args.iterations,
            warmup=args.warmup,
        ),
        benchmark_mode(
            name="sqlquery_load_all",
            runner=lambda: load_all_index.query(load_all_query),
            result_counter=count_sql_rows,
            iterations=args.iterations,
            warmup=args.warmup,
        ),
        benchmark_mode(
            name="redis_py_ft_search",
            runner=lambda: client.execute_command(*FT_SEARCH_ARGS),
            result_counter=count_ft_search_rows,
            iterations=args.iterations,
            warmup=args.warmup,
        ),
    ]

    df = pd.DataFrame(results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output, index=False)

    print(df.to_string(index=False))
    print(f"\nSaved results to {args.output}")


if __name__ == "__main__":
    main()
