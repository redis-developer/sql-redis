"""Tests for the RediSearch query builder component."""

import pytest

from sql_redis.query_builder import QueryBuilder


class TestQueryBuilderTextFields:
    """Tests for building TEXT field query syntax."""

    def test_text_single_term(self):
        """TEXT field with single term: @field:term."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "=", "laptop")
        
        assert result == "@title:laptop"

    def test_text_exact_phrase(self):
        """TEXT field with phrase: @field:"exact phrase"."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "=", "gaming laptop")
        
        assert result == '@title:"gaming laptop"'

    def test_text_match_term(self):
        """TEXT field with MATCH: @field:term."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "MATCH", "laptop")
        
        assert result == "@title:laptop"

    def test_text_prefix_search(self):
        """TEXT field with prefix: @field:prefix*."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "LIKE", "lap%")
        
        assert result == "@title:lap*"

    def test_text_negation(self):
        """TEXT field with NOT: -@field:term."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "MATCH", "refurbished", negated=True)
        
        assert result == "-@title:refurbished"

    def test_text_fuzzy_match(self):
        """TEXT field with fuzzy: @field:%term%."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "FUZZY", "laptap")
        
        assert result == "@title:%laptap%"

    def test_text_multi_field(self):
        """TEXT multi-field search: (@field1|field2:term)."""
        builder = QueryBuilder()
        result = builder.build_text_condition(["title", "description"], "MATCH", "wireless")
        
        assert result == "(@title|description:wireless)"


class TestQueryBuilderTagFields:
    """Tests for building TAG field query syntax."""

    def test_tag_equality(self):
        """TAG field with equality: @field:{value}."""
        builder = QueryBuilder()
        result = builder.build_tag_condition("category", "=", "electronics")
        
        assert result == "@category:{electronics}"

    def test_tag_in_clause(self):
        """TAG field with IN: @field:{val1|val2}."""
        builder = QueryBuilder()
        result = builder.build_tag_condition("tags", "IN", ["sale", "featured"])
        
        assert result == "@tags:{sale|featured}"

    def test_tag_not_equal(self):
        """TAG field with NOT: -@field:{value}."""
        builder = QueryBuilder()
        result = builder.build_tag_condition("category", "!=", "electronics")
        
        assert result == "-@category:{electronics}"

    def test_tag_escaping(self):
        """TAG values with special characters are escaped."""
        builder = QueryBuilder()
        result = builder.build_tag_condition("category", "=", "home-office")
        
        # Hyphens and other special chars need escaping in TAG
        assert result == r"@category:{home\-office}"


class TestQueryBuilderNumericFields:
    """Tests for building NUMERIC field query syntax."""

    def test_numeric_equals(self):
        """NUMERIC field with equality: @field:[val val]."""
        builder = QueryBuilder()
        result = builder.build_numeric_condition("price", "=", 100)
        
        assert result == "@price:[100 100]"

    def test_numeric_greater_than(self):
        """NUMERIC field with >: @field:[(val +inf]."""
        builder = QueryBuilder()
        result = builder.build_numeric_condition("price", ">", 100)
        
        assert result == "@price:[(100 +inf]"

    def test_numeric_greater_equal(self):
        """NUMERIC field with >=: @field:[val +inf]."""
        builder = QueryBuilder()
        result = builder.build_numeric_condition("price", ">=", 100)
        
        assert result == "@price:[100 +inf]"

    def test_numeric_less_than(self):
        """NUMERIC field with <: @field:[-inf (val]."""
        builder = QueryBuilder()
        result = builder.build_numeric_condition("price", "<", 100)
        
        assert result == "@price:[-inf (100]"

    def test_numeric_less_equal(self):
        """NUMERIC field with <=: @field:[-inf val]."""
        builder = QueryBuilder()
        result = builder.build_numeric_condition("price", "<=", 100)
        
        assert result == "@price:[-inf 100]"

    def test_numeric_between(self):
        """NUMERIC field with BETWEEN: @field:[min max]."""
        builder = QueryBuilder()
        result = builder.build_numeric_condition("price", "BETWEEN", (100, 500))
        
        assert result == "@price:[100 500]"

    def test_numeric_not_equal(self):
        """NUMERIC field with !=: -@field:[val val]."""
        builder = QueryBuilder()
        result = builder.build_numeric_condition("price", "!=", 100)

        assert result == "-@price:[100 100]"

    def test_numeric_unknown_operator(self):
        """NUMERIC field with unknown operator raises ValueError."""
        builder = QueryBuilder()
        with pytest.raises(ValueError, match="Unknown numeric operator"):
            builder.build_numeric_condition("price", "LIKE", 100)


class TestQueryBuilderVectorFields:
    """Tests for building VECTOR field query syntax."""

    def test_vector_knn(self):
        """VECTOR field KNN search."""
        builder = QueryBuilder()
        result = builder.build_vector_condition("embedding", k=5, alias="similarity")
        
        assert result == "=>[KNN 5 @embedding $BLOB AS similarity]"

    def test_vector_knn_with_prefilter(self):
        """VECTOR field KNN with pre-filter."""
        builder = QueryBuilder()
        prefilter = "@category:{electronics}"
        result = builder.build_vector_condition(
            "embedding", k=5, alias="score", prefilter=prefilter
        )
        
        assert result == "(@category:{electronics})=>[KNN 5 @embedding $BLOB AS score]"

    def test_vector_knn_all_documents(self):
        """VECTOR field KNN with * filter."""
        builder = QueryBuilder()
        result = builder.build_vector_condition("embedding", k=10, alias="dist")

        assert result == "=>[KNN 10 @embedding $BLOB AS dist]"


class TestQueryBuilderGeoFields:
    """Tests for building GEO field query syntax."""

    def test_geo_distance_filter(self):
        """GEO field with distance filter: GEOFILTER."""
        builder = QueryBuilder()
        result = builder.build_geo_filter("location", lon=-122.4, lat=37.8, radius=10, unit="km")

        assert result == "GEOFILTER location -122.4 37.8 10 km"

    def test_geo_distance_apply(self):
        """GEO field distance as computed field."""
        builder = QueryBuilder()
        result = builder.build_geo_distance_apply("location", lon=-122.4, lat=37.8, alias="dist")

        assert result == 'APPLY "geodistance(@location, -122.4, 37.8)" AS dist'

    def test_geo_distance_in_miles(self):
        """GEO field distance converted to miles."""
        builder = QueryBuilder()
        result = builder.build_geo_distance_apply(
            "location", lon=-73.99, lat=40.75, alias="miles", unit="mi"
        )

        # geodistance returns meters, divide by 1609.34 for miles
        assert "1609.34" in result

    def test_geo_distance_in_km(self):
        """GEO field distance converted to kilometers."""
        builder = QueryBuilder()
        result = builder.build_geo_distance_apply(
            "location", lon=-122.4, lat=37.8, alias="km_dist", unit="km"
        )

        assert "/1000" in result

    def test_geo_distance_in_feet(self):
        """GEO field distance converted to feet."""
        builder = QueryBuilder()
        result = builder.build_geo_distance_apply(
            "location", lon=-122.4, lat=37.8, alias="ft_dist", unit="ft"
        )

        assert "*3.28084" in result


class TestQueryBuilderBooleanCombinations:
    """Tests for combining conditions with boolean logic."""

    def test_and_conditions(self):
        """AND conditions: space-separated or parenthesized."""
        builder = QueryBuilder()
        conditions = ["@title:laptop", "@price:[0 1000]"]
        result = builder.combine_conditions(conditions, operator="AND")

        # Could be "@title:laptop @price:[0 1000]" or "(@title:laptop @price:[0 1000])"
        assert "@title:laptop" in result
        assert "@price:[0 1000]" in result

    def test_or_conditions(self):
        """OR conditions: pipe-separated in parentheses."""
        builder = QueryBuilder()
        conditions = ["@category:{books}", "@category:{electronics}"]
        result = builder.combine_conditions(conditions, operator="OR")

        assert "|" in result
        assert "(" in result and ")" in result

    def test_nested_boolean(self):
        """Nested boolean: (A AND B) OR C."""
        builder = QueryBuilder()
        inner = builder.combine_conditions(
            ["@title:laptop", "@price:[0 1000]"], operator="AND"
        )
        result = builder.combine_conditions(
            [inner, "@category:{accessories}"], operator="OR"
        )

        assert "|" in result

    def test_negation_in_combination(self):
        """Negation within combination: A AND NOT B."""
        builder = QueryBuilder()
        conditions = ["@title:laptop", "-@title:refurbished"]
        result = builder.combine_conditions(conditions, operator="AND")

        assert "-@title:refurbished" in result


class TestQueryBuilderFullQuery:
    """Tests for building complete query strings."""

    def test_simple_text_query(self):
        """Build simple text search query."""
        builder = QueryBuilder()
        result = builder.build_query_string(
            text_conditions=[("title", "MATCH", "laptop")],
            field_types={"title": "TEXT"}
        )

        assert result == "@title:laptop"

    def test_combined_query(self):
        """Build combined text + numeric + tag query."""
        builder = QueryBuilder()
        result = builder.build_query_string(
            text_conditions=[("title", "MATCH", "laptop")],
            numeric_conditions=[("price", "<", 1000)],
            tag_conditions=[("category", "=", "electronics")],
            field_types={
                "title": "TEXT",
                "price": "NUMERIC",
                "category": "TAG"
            }
        )

        assert "@title:laptop" in result
        assert "@price:" in result
        assert "@category:{electronics}" in result

    def test_wildcard_query(self):
        """Build wildcard query for no conditions."""
        builder = QueryBuilder()
        result = builder.build_query_string(field_types={})

        assert result == "*"

