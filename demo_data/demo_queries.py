"""
Demo Queries: Comparing RedisVL DSL vs SQL-to-Redis

This script demonstrates the same queries using:
1. Current approach: RedisVL Python DSL (filters, VectorQuery, etc.)
2. Proposed approach: SQL syntax via sql-redis translator

Run load_demo_data.py first to populate the indexes.
"""

import os
from redisvl.index import SearchIndex
from redisvl.query import FilterQuery, VectorQuery, CountQuery
from redisvl.query.filter import Tag, Num, Text, Geo, GeoRadius

# For SQL approach
from sql_redis import Executor
from sql_redis.schema import SchemaRegistry
from redis import Redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# ============================================================================
# Setup
# ============================================================================
client = Redis.from_url(REDIS_URL)
products_index = SearchIndex.from_existing("products", redis_url=REDIS_URL)
users_index = SearchIndex.from_existing("users", redis_url=REDIS_URL)

# SQL executor
registry = SchemaRegistry(client)
registry.load_all()
executor = Executor(client, registry)


def print_comparison(title: str, redisvl_results: list, sql_results):
    """Print side-by-side comparison of results."""
    print(f"\n{'=' * 70}")
    print(f"QUERY: {title}")
    print("=" * 70)
    print(f"\nRedisVL Results ({len(redisvl_results)} rows):")
    for r in redisvl_results[:3]:
        print(f"  {r}")
    print(f"\nSQL Results ({len(sql_results.rows)} rows):")
    for r in sql_results.rows[:3]:
        print(f"  {r}")


# ============================================================================
# Query 1: Simple filter - Find electronics under $100
# ============================================================================
def query_1_electronics_under_100():
    """Find electronics with price < $100"""
    
    # --- CURRENT: RedisVL DSL ---
    filter_expr = (Tag("category") == "electronics") & (Num("price") < 100)
    query = FilterQuery(
        filter_expression=filter_expr,
        return_fields=["title", "category", "price"]
    )
    redisvl_results = products_index.query(query)
    
    # --- PROPOSED: SQL ---
    sql = """
        SELECT title, category, price
        FROM products
        WHERE category = 'electronics' AND price < 100
    """
    sql_results = executor.execute(sql)
    
    print_comparison("Find electronics under $100", redisvl_results, sql_results)
    

# ============================================================================
# Query 2: Sorting and limiting - Top 5 highest rated products
# ============================================================================
def query_2_top_rated():
    """Get top 5 highest rated products"""
    
    # --- CURRENT: RedisVL DSL ---
    query = FilterQuery(
        return_fields=["title", "rating", "price"],
        num_results=5
    )
    query.sort_by("rating", asc=False)
    redisvl_results = products_index.query(query)
    
    # --- PROPOSED: SQL ---
    sql = """
        SELECT title, rating, price
        FROM products
        ORDER BY rating DESC
        LIMIT 5
    """
    sql_results = executor.execute(sql)
    
    print_comparison("Top 5 highest rated products", redisvl_results, sql_results)


# ============================================================================
# Query 3: Aggregation - Average price by category
# ============================================================================
def query_3_avg_price_by_category():
    """Calculate average price by category"""
    
    # --- CURRENT: RedisVL (requires raw aggregation) ---
    import redis.commands.search.reducers as reducers
    from redisvl.query.aggregate import AggregationQuery
    
    agg_query = AggregationQuery("*").group_by(
        "@category",
        reducers.avg("price").alias("avg_price")
    )
    redisvl_results = products_index.aggregate(agg_query)
    
    # --- PROPOSED: SQL ---
    sql = """
        SELECT category, AVG(price) AS avg_price
        FROM products
        GROUP BY category
    """
    sql_results = executor.execute(sql)
    
    print(f"\n{'=' * 70}")
    print("QUERY: Average price by category")
    print("=" * 70)
    print(f"\nRedisVL (Aggregation) Results:")
    for r in redisvl_results.rows[:5]:
        print(f"  {r}")
    print(f"\nSQL Results:")
    for r in sql_results.rows[:5]:
        print(f"  {r}")


# ============================================================================
# Query 4: Complex filter - Users in SF with high credit score, age 25-45
# ============================================================================
def query_4_complex_user_filter():
    """Find users in San Francisco with high credit score, age 25-45"""
    
    # --- CURRENT: RedisVL DSL ---
    filter_expr = (
        (Tag("city") == "San Francisco") &
        (Tag("credit_score") == "high") &
        (Num("age") >= 25) &
        (Num("age") <= 45)
    )
    query = FilterQuery(
        filter_expression=filter_expr,
        return_fields=["name", "age", "job", "credit_score", "city"]
    )
    redisvl_results = users_index.query(query)
    
    # --- PROPOSED: SQL ---
    sql = """
        SELECT name, age, job, credit_score, city
        FROM users
        WHERE city = 'San Francisco' 
          AND credit_score = 'high'
          AND age BETWEEN 25 AND 45
    """
    sql_results = executor.execute(sql)
    
    print_comparison("Users in SF, high credit, age 25-45", redisvl_results, sql_results)


# ============================================================================
# Main - Run all comparisons
# ============================================================================
def main():
    print("\n" + "=" * 70)
    print("DEMO: RedisVL DSL vs SQL-to-Redis")
    print("=" * 70)
    
    query_1_electronics_under_100()
    query_2_top_rated()
    query_3_avg_price_by_category()
    query_4_complex_user_filter()
    
    print("\n" + "=" * 70)
    print("SUMMARY: Key Tradeoffs")
    print("=" * 70)
    print("""
RedisVL DSL (Current):
  ✓ Type-safe with IDE support
  ✓ Pythonic, composable filters
  ✓ Direct control over Redis commands
  ✗ Learning curve for new users
  ✗ Verbose for complex queries
  ✗ Different syntax than SQL tools

SQL (Proposed):
  ✓ Universal - everyone knows SQL
  ✓ Concise, readable queries
  ✓ Easy migration from RDBMS
  ✓ Works with SQL tools and ORMs
  ✗ Less type safety
  ✗ Translation overhead
  ✗ Some Redis features not expressible in SQL
""")


if __name__ == "__main__":
    main()

