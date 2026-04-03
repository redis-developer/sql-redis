"""
Demo Data Loader for Redis

This script loads sample data into Redis and demonstrates both:
1. The current RedisVL approach (Python DSL with filters and queries)
2. The proposed SQL-to-Redis approach

Prerequisites:
    - Redis server running on localhost:6379
    - pip install redisvl

Usage:
    python load_demo_data.py
"""

import json
import os
from pathlib import Path

# Optional: Generate embeddings for vector search
try:
    from redisvl.utils.vectorize import HFTextVectorizer
    HAS_VECTORIZER = True
except ImportError:
    HAS_VECTORIZER = False
    print("Note: HuggingFace vectorizer not available. Skipping embedding generation.")

from redisvl.index import SearchIndex
from schemas import PRODUCTS_SCHEMA, USERS_SCHEMA

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
DATA_DIR = Path(__file__).parent


def load_json_data(filename: str) -> list[dict]:
    """Load data from a JSON file."""
    filepath = DATA_DIR / filename
    with open(filepath, "r") as f:
        return json.load(f)


def add_embeddings(data: list[dict], text_field: str, embedding_field: str = "embedding"):
    """Add vector embeddings to the data using HuggingFace model."""
    if not HAS_VECTORIZER:
        return data
    
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    
    vectorizer = HFTextVectorizer(
        model="sentence-transformers/all-MiniLM-L6-v2"
    )
    
    texts = [item[text_field] for item in data]
    embeddings = vectorizer.embed_many(texts, as_buffer=True)
    
    for item, embedding in zip(data, embeddings):
        item[embedding_field] = embedding
    
    return data


def create_and_load_index(schema: dict, data: list[dict], id_field: str = None):
    """Create a Redis index and load data."""
    index = SearchIndex.from_dict(schema, redis_url=REDIS_URL)
    
    # Create index (overwrite if exists)
    index.create(overwrite=True)
    print(f"Created index: {schema['index']['name']}")
    
    # Load data
    if id_field:
        keys = index.load(data, id_field=id_field)
    else:
        keys = index.load(data)
    
    print(f"Loaded {len(keys)} records")
    return index


def main():
    print("=" * 60)
    print("Loading Demo Data into Redis")
    print("=" * 60)
    
    # Load Products
    print("\n--- Loading Products ---")
    products = load_json_data("products.json")
    
    # Add embeddings to products (based on description)
    if HAS_VECTORIZER:
        print("Generating embeddings for product descriptions...")
        products = add_embeddings(products, "description", "embedding")
    
    products_index = create_and_load_index(PRODUCTS_SCHEMA, products, id_field="id")
    
    # Load Users
    print("\n--- Loading Users ---")
    users = load_json_data("users.json")
    users_index = create_and_load_index(USERS_SCHEMA, users, id_field="user_id")
    
    print("\n" + "=" * 60)
    print("Data loading complete!")
    print("=" * 60)
    
    # Print index info
    print("\nIndex Statistics:")
    print(f"  Products: {products_index.info().get('num_docs', 'N/A')} documents")
    print(f"  Users: {users_index.info().get('num_docs', 'N/A')} documents")
    
    print("\nYou can now run queries against these indexes.")
    print("Open Redis Insight to explore the data.")
    
    return products_index, users_index


if __name__ == "__main__":
    main()

