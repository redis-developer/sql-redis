"""Benchmark: Schema caching impact on SQL query performance.

Measures real Redis round-trips and latency across four modes:
  1. current redisvl     — load_all() on EVERY query (current production behavior)
  2. load_all (baseline) — load_all() once upfront, reuse registry
  3. lazy (no cache)     — lazy get_schema() but fresh registry per query
  4. lazy + cached       — lazy get_schema() with single reused registry

Uses a CommandCounter wrapper around execute_command to count actual
Redis round-trips (FT._LIST, FT.INFO, FT.SEARCH, FT.AGGREGATE) — not
estimated or expected counts.

Reusable: all parameters are configurable via CLI args. Requires a running
Redis instance with the RediSearch module.

Usage:
    python benchmarks/bench_schema_caching.py
    python benchmarks/bench_schema_caching.py --queries 100 1000 5000 10000
    python benchmarks/bench_schema_caching.py --redis-url redis://myhost:6379
    python benchmarks/bench_schema_caching.py --bg-indexes 20
    python benchmarks/bench_schema_caching.py --runs 5
    python benchmarks/bench_schema_caching.py --no-graphs
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from dataclasses import dataclass, field

import redis

from sql_redis.executor import Executor
from sql_redis.schema import SchemaRegistry


# ---------------------------------------------------------------------------
# Command Counter — wraps execute_command to count real round-trips
# ---------------------------------------------------------------------------
class CommandCounter:
    """Wraps a Redis client to count actual execute_command calls."""

    def __init__(self, client: redis.Redis):
        self.counts: dict[str, int] = {}
        self.total: int = 0
        self._original = client.execute_command

        def counting_execute(*args, **kwargs):
            cmd_name = str(args[0]) if args else "UNKNOWN"
            self.counts[cmd_name] = self.counts.get(cmd_name, 0) + 1
            self.total += 1
            return self._original(*args, **kwargs)

        client.execute_command = counting_execute

    def reset(self):
        self.counts.clear()
        self.total = 0


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------
@dataclass
class BenchmarkResult:
    mode: str
    num_queries: int
    total_ms: float
    per_query_ms: list[float] = field(default_factory=list)
    command_counts: dict[str, int] = field(default_factory=dict)
    total_commands: int = 0

    @property
    def mean_ms(self) -> float:
        return statistics.mean(self.per_query_ms) if self.per_query_ms else 0

    @property
    def p50_ms(self) -> float:
        return statistics.median(self.per_query_ms) if self.per_query_ms else 0

    @property
    def p95_ms(self) -> float:
        if not self.per_query_ms:
            return 0
        sorted_vals = sorted(self.per_query_ms)
        idx = int(len(sorted_vals) * 0.95)
        return sorted_vals[min(idx, len(sorted_vals) - 1)]

    @property
    def p99_ms(self) -> float:
        if not self.per_query_ms:
            return 0
        sorted_vals = sorted(self.per_query_ms)
        idx = int(len(sorted_vals) * 0.99)
        return sorted_vals[min(idx, len(sorted_vals) - 1)]


# ---------------------------------------------------------------------------
# SQL queries to benchmark (round-robin)
# ---------------------------------------------------------------------------
BENCH_QUERIES = [
    # Tag filter (FT.SEARCH)
    "SELECT name, price FROM bench_products WHERE category = 'electronics'",
    # Numeric range (FT.SEARCH)
    "SELECT name, price FROM bench_products WHERE price BETWEEN 10 AND 100",
    # Text search (FT.SEARCH)
    "SELECT name, title FROM bench_products WHERE title = 'laptop*'",
    # Aggregation (FT.AGGREGATE)
    "SELECT category, COUNT(*) as cnt, AVG(price) as avg_price FROM bench_products GROUP BY category",
    # Combined filter (FT.SEARCH)
    "SELECT name FROM bench_products WHERE category = 'electronics' AND price < 500",
]


# ---------------------------------------------------------------------------
# Setup: create indexes and load data
# ---------------------------------------------------------------------------
def setup_redis(client: redis.Redis, num_background_indexes: int = 10) -> None:
    """Create target index + background indexes with sample data."""
    # Clean up any existing indexes
    try:
        existing = client.execute_command("FT._LIST")
        for idx in existing:
            idx_name = idx.decode("utf-8") if isinstance(idx, bytes) else idx
            try:
                client.execute_command("FT.DROPINDEX", idx_name, "DD")
            except redis.ResponseError:
                pass
    except redis.ResponseError:
        pass

    # Create the target index
    client.execute_command(
        "FT.CREATE", "bench_products", "ON", "HASH",
        "PREFIX", "1", "bench_product:",
        "SCHEMA",
        "title", "TEXT", "SORTABLE",
        "name", "TEXT", "SORTABLE",
        "price", "NUMERIC", "SORTABLE",
        "stock", "NUMERIC", "SORTABLE",
        "category", "TAG", "SORTABLE",
    )

    # Load sample data
    products = [
        {"title": "Gaming laptop Pro", "name": "Gaming Laptop", "price": 899, "stock": 10, "category": "electronics"},
        {"title": "Budget laptop Basic", "name": "Budget Laptop", "price": 499, "stock": 25, "category": "electronics"},
        {"title": "Premium laptop Ultra", "name": "Premium Laptop", "price": 1299, "stock": 5, "category": "electronics"},
        {"title": "Python Programming", "name": "Python Book", "price": 45, "stock": 100, "category": "books"},
        {"title": "Redis in Action", "name": "Redis Book", "price": 55, "stock": 50, "category": "books"},
        {"title": "Wireless Mouse", "name": "Mouse", "price": 29, "stock": 200, "category": "electronics"},
        {"title": "Mechanical Keyboard", "name": "Keyboard", "price": 149, "stock": 75, "category": "electronics"},
        {"title": "Monitor Stand", "name": "Stand", "price": 89, "stock": 40, "category": "accessories"},
        {"title": "Desk Lamp", "name": "Lamp", "price": 35, "stock": 80, "category": "accessories"},
        {"title": "Notebook Set", "name": "Notebooks", "price": 15, "stock": 300, "category": "stationery"},
    ]
    for i, p in enumerate(products):
        client.hset(f"bench_product:{i+1}", mapping=p)

    # Create background indexes to simulate a realistic multi-index server
    for i in range(num_background_indexes):
        idx_name = f"bg_index_{i}"
        prefix = f"bg_{i}:"
        client.execute_command(
            "FT.CREATE", idx_name, "ON", "HASH",
            "PREFIX", "1", prefix,
            "SCHEMA",
            "field_a", "TEXT",
            "field_b", "NUMERIC",
            "field_c", "TAG",
            "field_d", "TEXT",
            "field_e", "NUMERIC",
        )


# ---------------------------------------------------------------------------
# Benchmark runners
# ---------------------------------------------------------------------------
def _build_query_list(num_queries: int) -> list[str]:
    """Build round-robin list of queries."""
    return [BENCH_QUERIES[i % len(BENCH_QUERIES)] for i in range(num_queries)]


def run_load_all(client: redis.Redis, counter: CommandCounter, queries: list[str]) -> BenchmarkResult:
    """Mode 1: load_all() upfront, then execute all queries."""
    counter.reset()
    per_query_ms = []

    registry = SchemaRegistry(client)
    overall_start = time.perf_counter()
    registry.load_all()

    for sql in queries:
        executor = Executor(client, registry)
        t0 = time.perf_counter()
        executor.execute(sql)
        per_query_ms.append((time.perf_counter() - t0) * 1000)

    total_ms = (time.perf_counter() - overall_start) * 1000

    return BenchmarkResult(
        mode="load_all (baseline)",
        num_queries=len(queries),
        total_ms=total_ms,
        per_query_ms=per_query_ms,
        command_counts=dict(counter.counts),
        total_commands=counter.total,
    )


def run_current_redisvl(client: redis.Redis, counter: CommandCounter, queries: list[str]) -> BenchmarkResult:
    """Mode 2: current redisvl behavior — load_all() on every query."""
    counter.reset()
    per_query_ms = []

    overall_start = time.perf_counter()
    for sql in queries:
        registry = SchemaRegistry(client)
        registry.load_all()
        executor = Executor(client, registry)
        t0 = time.perf_counter()
        executor.execute(sql)
        per_query_ms.append((time.perf_counter() - t0) * 1000)

    total_ms = (time.perf_counter() - overall_start) * 1000

    return BenchmarkResult(
        mode="current redisvl",
        num_queries=len(queries),
        total_ms=total_ms,
        per_query_ms=per_query_ms,
        command_counts=dict(counter.counts),
        total_commands=counter.total,
    )


def run_lazy_no_cache(client: redis.Redis, counter: CommandCounter, queries: list[str]) -> BenchmarkResult:
    """Mode 3: fresh SchemaRegistry per query (no instance-level cache)."""
    counter.reset()
    per_query_ms = []

    overall_start = time.perf_counter()
    for sql in queries:
        registry = SchemaRegistry(client)
        executor = Executor(client, registry)
        t0 = time.perf_counter()
        executor.execute(sql)
        per_query_ms.append((time.perf_counter() - t0) * 1000)

    total_ms = (time.perf_counter() - overall_start) * 1000

    return BenchmarkResult(
        mode="lazy (no cache)",
        num_queries=len(queries),
        total_ms=total_ms,
        per_query_ms=per_query_ms,
        command_counts=dict(counter.counts),
        total_commands=counter.total,
    )


def run_lazy_cached(client: redis.Redis, counter: CommandCounter, queries: list[str]) -> BenchmarkResult:
    """Mode 4: single SchemaRegistry reused across all queries (lazy + cached)."""
    counter.reset()
    per_query_ms = []

    registry = SchemaRegistry(client)
    executor = Executor(client, registry)
    overall_start = time.perf_counter()

    for sql in queries:
        t0 = time.perf_counter()
        executor.execute(sql)
        per_query_ms.append((time.perf_counter() - t0) * 1000)

    total_ms = (time.perf_counter() - overall_start) * 1000

    return BenchmarkResult(
        mode="lazy + cached",
        num_queries=len(queries),
        total_ms=total_ms,
        per_query_ms=per_query_ms,
        command_counts=dict(counter.counts),
        total_commands=counter.total,
    )


# ---------------------------------------------------------------------------
# Display results
# ---------------------------------------------------------------------------
def print_results(results: list[BenchmarkResult]) -> None:
    """Print results as a formatted table."""
    header = f"{'Mode':<22} {'Queries':>7} {'Total ms':>10} {'Mean ms':>8} {'p50 ms':>7} {'p95 ms':>7} {'p99 ms':>7} {'Cmds':>6} {'FT._LIST':>8} {'FT.INFO':>7} {'FT.SEARCH':>9} {'FT.AGG':>6}"
    sep = "-" * len(header)
    print(f"\n{sep}\n{header}\n{sep}")
    for r in results:
        ft_list = r.command_counts.get("FT._LIST", 0)
        ft_info = r.command_counts.get("FT.INFO", 0)
        ft_search = r.command_counts.get("FT.SEARCH", 0)
        ft_agg = r.command_counts.get("FT.AGGREGATE", 0)
        print(
            f"{r.mode:<22} {r.num_queries:>7} {r.total_ms:>10.1f} {r.mean_ms:>8.3f} "
            f"{r.p50_ms:>7.3f} {r.p95_ms:>7.3f} {r.p99_ms:>7.3f} {r.total_commands:>6} "
            f"{ft_list:>8} {ft_info:>7} {ft_search:>9} {ft_agg:>6}"
        )
    print(sep)


# ---------------------------------------------------------------------------
# Graphing
# ---------------------------------------------------------------------------
def generate_graphs(all_results: list[BenchmarkResult], output_dir: str = "benchmarks") -> None:
    """Generate comparison charts from benchmark results."""
    import matplotlib.pyplot as plt
    import numpy as np

    # Group results by query count
    query_counts = sorted(set(r.num_queries for r in all_results))
    modes = ["current redisvl", "load_all (baseline)", "lazy (no cache)", "lazy + cached"]
    colors = {"current redisvl": "#8e44ad", "load_all (baseline)": "#e74c3c", "lazy (no cache)": "#f39c12", "lazy + cached": "#2ecc71"}

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle("Schema Caching Benchmark Results", fontsize=14, fontweight="bold")

    # --- Chart 1: Total Redis Commands ---
    ax = axes[0, 0]
    x = np.arange(len(query_counts))
    width = 0.2
    for i, mode in enumerate(modes):
        vals = [next((r.total_commands for r in all_results if r.mode == mode and r.num_queries == q), 0) for q in query_counts]
        ax.bar(x + i * width, vals, width, label=mode, color=colors[mode])
    ax.set_xlabel("Number of Queries")
    ax.set_ylabel("Total Redis Commands")
    ax.set_title("Real Redis Round-Trips")
    ax.set_xticks(x + width)
    ax.set_xticklabels(query_counts)
    ax.legend(fontsize=8)

    # --- Chart 2: FT.INFO calls ---
    ax = axes[0, 1]
    for i, mode in enumerate(modes):
        vals = [next((r.command_counts.get("FT.INFO", 0) for r in all_results if r.mode == mode and r.num_queries == q), 0) for q in query_counts]
        ax.bar(x + i * width, vals, width, label=mode, color=colors[mode])
    ax.set_xlabel("Number of Queries")
    ax.set_ylabel("FT.INFO Calls")
    ax.set_title("Schema Loading Overhead (FT.INFO)")
    ax.set_xticks(x + width)
    ax.set_xticklabels(query_counts)
    ax.legend(fontsize=8)

    # --- Chart 3: Mean per-query latency ---
    ax = axes[1, 0]
    for i, mode in enumerate(modes):
        vals = [next((r.mean_ms for r in all_results if r.mode == mode and r.num_queries == q), 0) for q in query_counts]
        ax.bar(x + i * width, vals, width, label=mode, color=colors[mode])
    ax.set_xlabel("Number of Queries")
    ax.set_ylabel("Mean Latency (ms)")
    ax.set_title("Per-Query Latency (Mean)")
    ax.set_xticks(x + width)
    ax.set_xticklabels(query_counts)
    ax.legend(fontsize=8)

    # --- Chart 4: Total wall-clock time ---
    ax = axes[1, 1]
    for i, mode in enumerate(modes):
        vals = [next((r.total_ms for r in all_results if r.mode == mode and r.num_queries == q), 0) for q in query_counts]
        ax.bar(x + i * width, vals, width, label=mode, color=colors[mode])
    ax.set_xlabel("Number of Queries")
    ax.set_ylabel("Total Time (ms)")
    ax.set_title("Total Wall-Clock Time")
    ax.set_xticks(x + width)
    ax.set_xticklabels(query_counts)
    ax.legend(fontsize=8)

    plt.tight_layout()
    graph_path = f"{output_dir}/benchmark_results.png"
    plt.savefig(graph_path, dpi=150)
    plt.close()
    print(f"\nGraph saved to {graph_path}")



# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
def cleanup_redis(client: redis.Redis) -> None:
    """Remove all benchmark indexes and data."""
    try:
        existing = client.execute_command("FT._LIST")
        for idx in existing:
            idx_name = idx.decode("utf-8") if isinstance(idx, bytes) else idx
            if idx_name.startswith("bench_") or idx_name.startswith("bg_index_"):
                try:
                    client.execute_command("FT.DROPINDEX", idx_name, "DD")
                except redis.ResponseError:
                    pass
    except redis.ResponseError:
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark schema caching performance")
    parser.add_argument("--queries", nargs="+", type=int, default=[100, 1000], help="Query counts to benchmark")
    parser.add_argument("--redis-url", default="redis://localhost:6379", help="Redis URL")
    parser.add_argument("--bg-indexes", type=int, default=10, help="Number of background indexes")
    parser.add_argument("--runs", type=int, default=3, help="Runs per mode (report median)")
    parser.add_argument("--no-graphs", action="store_true", help="Skip graph generation")
    args = parser.parse_args()

    client = redis.Redis.from_url(args.redis_url, decode_responses=True)

    # Verify connection
    try:
        client.ping()
    except redis.ConnectionError:
        print(f"Cannot connect to Redis at {args.redis_url}")
        sys.exit(1)

    counter = CommandCounter(client)
    all_results: list[BenchmarkResult] = []

    print(f"Schema Caching Benchmark\n{'=' * 40}")
    print(f"Redis: {args.redis_url}")
    print(f"Background indexes: {args.bg_indexes}")
    print(f"Query counts: {args.queries}")
    print(f"Runs per mode: {args.runs}")

    for num_queries in args.queries:
        queries = _build_query_list(num_queries)
        print(f"\n--- {num_queries} queries ---")

        runners = [
            ("current redisvl", run_current_redisvl),
            ("load_all (baseline)", run_load_all),
            ("lazy (no cache)", run_lazy_no_cache),
            ("lazy + cached", run_lazy_cached),
        ]

        batch_results: list[BenchmarkResult] = []

        for mode_name, runner in runners:
            run_results = []
            for run_idx in range(args.runs):
                # Re-setup for each run to ensure clean state
                counter.reset()
                setup_redis(client, args.bg_indexes)
                counter.reset()  # Don't count setup commands

                result = runner(client, counter, queries)
                run_results.append(result)

            # Pick the median run by total_ms
            run_results.sort(key=lambda r: r.total_ms)
            median_result = run_results[len(run_results) // 2]
            batch_results.append(median_result)
            all_results.append(median_result)

        print_results(batch_results)

    # Generate graphs
    if not args.no_graphs:
        try:
            generate_graphs(all_results)
        except ImportError:
            print("\nmatplotlib not installed — skipping graph generation")

    cleanup_redis(client)
    client.close()


if __name__ == "__main__":
    main()