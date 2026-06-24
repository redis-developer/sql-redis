"""Pytest configuration and fixtures."""

import struct

import pytest
import redis
from testcontainers.redis import RedisContainer


@pytest.fixture(scope="module")
def redis_container():
    """Create a Redis 8 container for testing.

    Uses 8.4+ so FT.HYBRID (hybrid_vector_search) integration tests can run;
    older versions cause those tests to skip via a server-capability check.
    """
    with RedisContainer(image="redis:8.4") as container:
        yield container


@pytest.fixture(scope="module")
def redis_client(redis_container) -> redis.Redis:
    """Create a Redis client connected to the test container."""
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    client = redis.Redis(host=host, port=int(port), decode_responses=True)
    yield client
    client.close()


def float_vector_to_bytes(vector: list[float]) -> bytes:
    """Convert a list of floats to binary format for Redis vector storage."""
    return struct.pack(f"{len(vector)}f", *vector)


@pytest.fixture(scope="module")
def products_index(redis_client: redis.Redis):
    """Create the products search index with all required fields."""
    index_name = "products"

    # Drop index if exists
    try:
        redis_client.execute_command("FT.DROPINDEX", index_name, "DD")
    except redis.ResponseError:
        pass

    # Create index with all fields needed for the 10 test queries
    redis_client.execute_command(
        "FT.CREATE",
        index_name,
        "ON",
        "HASH",
        "PREFIX",
        "1",
        "product:",
        "SCHEMA",
        "title",
        "TEXT",
        "SORTABLE",
        "name",
        "TEXT",
        "SORTABLE",
        "price",
        "NUMERIC",
        "SORTABLE",
        "stock",
        "NUMERIC",
        "SORTABLE",
        "rating",
        "NUMERIC",
        "SORTABLE",
        "category",
        "TAG",
        "SORTABLE",
        "tags",
        "TAG",
    )

    return index_name


@pytest.fixture(scope="module")
def vec_index(redis_client: redis.Redis):
    """Create the vector search index."""
    index_name = "vec_index"

    # Drop index if exists
    try:
        redis_client.execute_command("FT.DROPINDEX", index_name, "DD")
    except redis.ResponseError:
        pass

    # Create index with vector field (4 dimensions for testing)
    redis_client.execute_command(
        "FT.CREATE",
        index_name,
        "ON",
        "HASH",
        "PREFIX",
        "1",
        "vec:",
        "SCHEMA",
        "id",
        "TEXT",
        "SORTABLE",
        "embedding",
        "VECTOR",
        "FLAT",
        "6",
        "TYPE",
        "FLOAT32",
        "DIM",
        "4",
        "DISTANCE_METRIC",
        "COSINE",
    )

    return index_name


@pytest.fixture(scope="module")
def items_index(redis_client: redis.Redis):
    """Create the items index for hybrid search testing."""
    index_name = "items"

    # Drop index if exists
    try:
        redis_client.execute_command("FT.DROPINDEX", index_name, "DD")
    except redis.ResponseError:
        pass

    # Create index with text, tag, and vector fields
    redis_client.execute_command(
        "FT.CREATE",
        index_name,
        "ON",
        "HASH",
        "PREFIX",
        "1",
        "item:",
        "SCHEMA",
        "name",
        "TEXT",
        "SORTABLE",
        "category",
        "TAG",
        "SORTABLE",
        "description",
        "TEXT",
        "embedding",
        "VECTOR",
        "FLAT",
        "6",
        "TYPE",
        "FLOAT32",
        "DIM",
        "4",
        "DISTANCE_METRIC",
        "COSINE",
    )

    return index_name


@pytest.fixture(scope="module")
def products_data(redis_client: redis.Redis, products_index: str):
    """Populate the products index with test data."""
    products = [
        # Laptops for text search test
        {
            "title": "Gaming laptop Pro",
            "name": "Gaming Laptop",
            "price": 899,
            "stock": 10,
            "rating": 4.5,
            "category": "electronics",
            "tags": "sale,featured",
        },
        {
            "title": "Budget laptop Basic",
            "name": "Budget Laptop",
            "price": 499,
            "stock": 25,
            "rating": 3.8,
            "category": "electronics",
            "tags": "sale",
        },
        {
            "title": "Premium laptop Ultra",
            "name": "Premium Laptop",
            "price": 1299,
            "stock": 5,
            "rating": 4.9,
            "category": "electronics",
            "tags": "featured",
        },
        # Books for category/pagination test
        {
            "title": "Python Programming",
            "name": "Python Book",
            "price": 45,
            "stock": 100,
            "rating": 4.7,
            "category": "books",
            "tags": "bestseller",
        },
        {
            "title": "Redis in Action",
            "name": "Redis Book",
            "price": 55,
            "stock": 50,
            "rating": 4.6,
            "category": "books",
            "tags": "featured",
        },
        {
            "title": "Data Science Guide",
            "name": "DS Book",
            "price": 65,
            "stock": 30,
            "rating": 4.4,
            "category": "books",
            "tags": "sale",
        },
        # More products for aggregation tests
        {
            "title": "Wireless Mouse",
            "name": "Mouse",
            "price": 29,
            "stock": 200,
            "rating": 4.2,
            "category": "electronics",
            "tags": "sale",
        },
        {
            "title": "Mechanical Keyboard",
            "name": "Keyboard",
            "price": 149,
            "stock": 75,
            "rating": 4.6,
            "category": "electronics",
            "tags": "featured",
        },
        {
            "title": "USB Hub",
            "name": "Hub",
            "price": 25,
            "stock": 150,
            "rating": 3.9,
            "category": "electronics",
            "tags": "sale",
        },
        {
            "title": "Monitor Stand",
            "name": "Stand",
            "price": 89,
            "stock": 40,
            "rating": 4.1,
            "category": "accessories",
            "tags": "sale,featured",
        },
        {
            "title": "Desk Lamp",
            "name": "Lamp",
            "price": 35,
            "stock": 80,
            "rating": 4.0,
            "category": "accessories",
            "tags": "sale",
        },
        {
            "title": "Notebook Set",
            "name": "Notebooks",
            "price": 15,
            "stock": 300,
            "rating": 4.3,
            "category": "stationery",
            "tags": "bestseller",
        },
    ]

    for i, product in enumerate(products):
        key = f"product:{i + 1}"
        redis_client.hset(key, mapping=product)

    return products_index


@pytest.fixture(scope="module")
def vec_data(redis_client: redis.Redis, vec_index: str):
    """Populate the vector index with test data."""
    vectors = [
        {"id": "v1", "embedding": float_vector_to_bytes([0.1, 0.2, 0.3, 0.4])},
        {"id": "v2", "embedding": float_vector_to_bytes([0.2, 0.3, 0.4, 0.5])},
        {"id": "v3", "embedding": float_vector_to_bytes([0.3, 0.4, 0.5, 0.6])},
        {"id": "v4", "embedding": float_vector_to_bytes([0.4, 0.5, 0.6, 0.7])},
        {"id": "v5", "embedding": float_vector_to_bytes([0.5, 0.6, 0.7, 0.8])},
        {"id": "v6", "embedding": float_vector_to_bytes([0.9, 0.1, 0.2, 0.3])},
    ]

    # Need a non-decode client for binary data
    host = redis_client.connection_pool.connection_kwargs["host"]
    port = redis_client.connection_pool.connection_kwargs["port"]
    binary_client = redis.Redis(host=host, port=port, decode_responses=False)

    for i, vec in enumerate(vectors):
        key = f"vec:{i + 1}"
        binary_client.hset(key, mapping=vec)

    binary_client.close()
    return vec_index


@pytest.fixture(scope="module")
def items_data(redis_client: redis.Redis, items_index: str):
    """Populate the items index with test data for hybrid search."""
    # Need a non-decode client for binary data
    host = redis_client.connection_pool.connection_kwargs["host"]
    port = redis_client.connection_pool.connection_kwargs["port"]
    binary_client = redis.Redis(host=host, port=port, decode_responses=False)

    items = [
        {
            "name": "iPhone 15",
            "category": "electronics",
            "description": "Latest smartphone with advanced features",
            "embedding": float_vector_to_bytes([0.1, 0.2, 0.3, 0.4]),
        },
        {
            "name": "Samsung Galaxy",
            "category": "electronics",
            "description": "Premium smartphone with great camera",
            "embedding": float_vector_to_bytes([0.15, 0.25, 0.35, 0.45]),
        },
        {
            "name": "Google Pixel",
            "category": "electronics",
            "description": "Smart smartphone with AI features",
            "embedding": float_vector_to_bytes([0.2, 0.3, 0.4, 0.5]),
        },
        {
            "name": "Leather Wallet",
            "category": "accessories",
            "description": "Premium leather wallet",
            "embedding": float_vector_to_bytes([0.8, 0.7, 0.6, 0.5]),
        },
        {
            "name": "Bluetooth Speaker",
            "category": "electronics",
            "description": "Portable speaker system",
            "embedding": float_vector_to_bytes([0.5, 0.4, 0.3, 0.2]),
        },
    ]

    for i, item in enumerate(items):
        key = f"item:{i + 1}"
        binary_client.hset(key, mapping=item)

    binary_client.close()
    return items_index
