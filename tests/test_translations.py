"""Tests for SQL to Redis command translations."""

import pytest

from sql_redis import translate_sql


class TestSimpleSelectWithTextSearch:
    """Test 1: Simple SELECT with WHERE (Text Search)."""

    def test_match_with_price_filter_and_ordering(self):
        sql = """
            SELECT title, price 
            FROM products 
            WHERE MATCH(title, 'laptop') AND price < 1000 
            ORDER BY price ASC 
            LIMIT 10
        """
        expected = (
            'FT.AGGREGATE products '
            '"@title:laptop @price:[0 (1000]" '
            'LOAD 2 @title @price '
            'SORTBY 2 @price ASC '
            'LIMIT 0 10 '
            'DIALECT 2'
        )
        assert translate_sql(sql) == expected


class TestVectorKNNSearch:
    """Test 2: Vector KNN Search."""

    def test_vector_distance_with_knn(self):
        sql = """
            SELECT id, vector_distance(embedding, $vector) AS similarity
            FROM vec_index
            ORDER BY similarity ASC
            LIMIT 5
        """
        expected = (
            'FT.AGGREGATE vec_index '
            '"*=>[KNN 5 @embedding $BLOB AS similarity]" '
            'LOAD 1 @id '
            'SORTBY 2 @similarity ASC '
            'LIMIT 0 5 '
            'PARAMS 2 BLOB <binary_vector> '
            'DIALECT 2'
        )
        assert translate_sql(sql) == expected


class TestHybridSearch:
    """Test 3: Hybrid Search (Text + Vector)."""

    def test_text_and_vector_combined(self):
        sql = """
            SELECT name, vector_distance(embedding, $vector) AS score
            FROM items
            WHERE category = 'electronics' 
              AND MATCH(description, 'smartphone')
            ORDER BY score ASC
            LIMIT 5
        """
        expected = (
            'FT.AGGREGATE items '
            '"(@category:{electronics} @description:smartphone)=>[KNN 5 @embedding $BLOB AS score]" '
            'LOAD 1 @name '
            'SORTBY 2 @score ASC '
            'LIMIT 0 5 '
            'PARAMS 2 BLOB <binary_vector> '
            'DIALECT 2'
        )
        assert translate_sql(sql) == expected


class TestGroupByWithCount:
    """Test 4: GROUP BY with COUNT."""

    def test_count_with_having_clause(self):
        sql = """
            SELECT category, COUNT(*) AS count
            FROM products
            WHERE price >= 50
            GROUP BY category
            HAVING count > 10
            ORDER BY count DESC
        """
        expected = (
            'FT.AGGREGATE products '
            '"@price:[50 +inf]" '
            'GROUPBY 1 @category '
            'REDUCE COUNT 0 AS count '
            'FILTER "@count > 10" '
            'SORTBY 2 @count DESC '
            'DIALECT 2'
        )
        assert translate_sql(sql) == expected


class TestMultipleAggregations:
    """Test 5: Multiple Aggregations."""

    def test_count_sum_avg_aggregations(self):
        sql = """
            SELECT category, 
                   COUNT(*) AS product_count,
                   SUM(price) AS total_price,
                   AVG(rating) AS avg_rating
            FROM products
            GROUP BY category
            ORDER BY total_price DESC
            LIMIT 10
        """
        expected = (
            'FT.AGGREGATE products "*" '
            'GROUPBY 1 @category '
            'REDUCE COUNT 0 AS product_count '
            'REDUCE SUM 1 @price AS total_price '
            'REDUCE AVG 1 @rating AS avg_rating '
            'SORTBY 2 @total_price DESC '
            'LIMIT 0 10 '
            'DIALECT 2'
        )
        assert translate_sql(sql) == expected


class TestGlobalAggregation:
    """Test 6: Global Aggregation (No GROUP BY)."""

    def test_aggregation_without_group_by(self):
        sql = """
            SELECT COUNT(*) AS total_count,
                   AVG(price) AS avg_price
            FROM products
            WHERE price > 100
        """
        expected = (
            'FT.AGGREGATE products '
            '"@price:[100 +inf]" '
            'GROUPBY 0 '
            'REDUCE COUNT 0 AS total_count '
            'REDUCE AVG 1 @price AS avg_price '
            'DIALECT 2'
        )
        assert translate_sql(sql) == expected


class TestApplyWithComputedFields:
    """Test 7: APPLY with Computed Fields."""

    def test_computed_field_expression(self):
        sql = """
            SELECT price,
                   (price * 0.9) AS discounted_price
            FROM products
            WHERE price > 100
            ORDER BY discounted_price DESC
        """
        expected = (
            'FT.AGGREGATE products '
            '"@price:[100 +inf]" '
            'LOAD 1 @price '
            'APPLY "@price * 0.9" AS discounted_price '
            'SORTBY 2 @discounted_price DESC '
            'DIALECT 2'
        )
        assert translate_sql(sql) == expected


class TestRangeQueryWithBetween:
    """Test 8: Range Query with BETWEEN."""

    def test_between_and_greater_than(self):
        sql = """
            SELECT title, price
            FROM products
            WHERE price BETWEEN 100 AND 500
              AND stock > 0
        """
        expected = (
            'FT.AGGREGATE products '
            '"@price:[100 500] @stock:[1 +inf]" '
            'LOAD 2 @title @price '
            'DIALECT 2'
        )
        assert translate_sql(sql) == expected


class TestTagFieldMultiValueSearch:
    """Test 9: Tag Field Multi-Value Search."""

    def test_in_clause_with_or(self):
        sql = """
            SELECT name
            FROM products
            WHERE tags IN ('sale', 'featured')
              OR category = 'electronics'
        """
        expected = (
            'FT.AGGREGATE products '
            '"(@tags:{sale|featured} | @category:{electronics})" '
            'LOAD 1 @name '
            'DIALECT 2'
        )
        assert translate_sql(sql) == expected


class TestPaginationWithOffset:
    """Test 10: Pagination with OFFSET."""

    def test_limit_with_offset(self):
        sql = """
            SELECT title, price
            FROM products
            WHERE category = 'books'
            ORDER BY price DESC
            LIMIT 20 OFFSET 40
        """
        expected = (
            'FT.AGGREGATE products '
            '"@category:{books}" '
            'LOAD 2 @title @price '
            'SORTBY 2 @price DESC '
            'LIMIT 40 20 '
            'DIALECT 2'
        )
        assert translate_sql(sql) == expected

