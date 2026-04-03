"""
Index schemas for Redis demo data.

These schemas are designed to be easy to read and use with RedisVL's SearchIndex.
They demonstrate the different field types available and how to configure them.

Usage:
    from schemas import PRODUCTS_SCHEMA, USERS_SCHEMA
    from redisvl.index import SearchIndex
    
    index = SearchIndex.from_dict(PRODUCTS_SCHEMA, redis_url="redis://localhost:6379")
    index.create(overwrite=True)
"""

# ============================================================================
# Products Index Schema
# - Demonstrates: TEXT, TAG, NUMERIC, and VECTOR fields
# - Use case: E-commerce product search with semantic search capability
# ============================================================================
PRODUCTS_SCHEMA = {
    "index": {
        "name": "products",
        "prefix": "products:",
        "storage_type": "hash"
    },
    "fields": [
        # TEXT field - full-text searchable, supports fuzzy matching
        {
            "name": "title",
            "type": "text",
            "attrs": {
                "sortable": True
            }
        },
        # TEXT field for description - good for full-text search
        {
            "name": "description",
            "type": "text"
        },
        # TAG fields - exact match, categorical data
        {
            "name": "category",
            "type": "tag",
            "attrs": {
                "sortable": True
            }
        },
        {
            "name": "brand",
            "type": "tag"
        },
        # NUMERIC fields - range queries, sorting, aggregations
        {
            "name": "price",
            "type": "numeric",
            "attrs": {
                "sortable": True
            }
        },
        {
            "name": "rating",
            "type": "numeric",
            "attrs": {
                "sortable": True
            }
        },
        {
            "name": "stock",
            "type": "numeric",
            "attrs": {
                "sortable": True
            }
        },
        # VECTOR field - for semantic/similarity search
        # Dimensions match sentence-transformers/all-MiniLM-L6-v2
        {
            "name": "embedding",
            "type": "vector",
            "attrs": {
                "dims": 384,
                "distance_metric": "cosine",
                "algorithm": "flat",
                "datatype": "float32"
            }
        }
    ]
}

# ============================================================================
# Users Index Schema  
# - Demonstrates: TAG, TEXT, NUMERIC, GEO fields
# - Use case: User profile search with location-based filtering
# ============================================================================
USERS_SCHEMA = {
    "index": {
        "name": "users",
        "prefix": "users:",
        "storage_type": "hash"
    },
    "fields": [
        # TAG field for exact user ID lookups
        {
            "name": "user_id",
            "type": "tag"
        },
        # TEXT field - searchable name
        {
            "name": "name",
            "type": "text",
            "attrs": {
                "sortable": True
            }
        },
        # TAG field - exact match email
        {
            "name": "email", 
            "type": "tag"
        },
        # NUMERIC field - for age range queries
        {
            "name": "age",
            "type": "numeric",
            "attrs": {
                "sortable": True
            }
        },
        # TEXT field - job title (searchable)
        {
            "name": "job",
            "type": "text"
        },
        # TAG field - categorical credit score
        {
            "name": "credit_score",
            "type": "tag"
        },
        # TAG field - city for exact matching
        {
            "name": "city",
            "type": "tag"
        },
        # GEO field - for location-based queries
        {
            "name": "office_location",
            "type": "geo"
        },
        # NUMERIC field - timestamp for date range queries
        {
            "name": "signup_date",
            "type": "numeric",
            "attrs": {
                "sortable": True
            }
        },
        # NUMERIC field - for aggregations and range queries
        {
            "name": "total_purchases",
            "type": "numeric",
            "attrs": {
                "sortable": True
            }
        }
    ]
}

