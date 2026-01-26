"""Tests for the SQL analyzer component."""

import pytest

from sql_redis.analyzer import AnalyzedQuery, Analyzer
from sql_redis.parser import SQLParser


@pytest.fixture
def sample_schema() -> dict[str, dict[str, str]]:
    """Sample schema for testing."""
    return {
        "products": {
            "title": "TEXT",
            "name": "TEXT",
            "description": "TEXT",
            "price": "NUMERIC",
            "stock": "NUMERIC",
            "rating": "NUMERIC",
            "category": "TAG",
            "tags": "TAG",
        },
        "vectors": {
            "id": "TEXT",
            "embedding": "VECTOR",
            "category": "TAG",
        },
        "stores": {
            "name": "TEXT",
            "location": "GEO",
        },
    }


class TestAnalyzerFieldTypeResolution:
    """Tests for resolving field types from schema."""

    def test_resolve_text_field(self, sample_schema):
        """Analyzer identifies TEXT fields."""
        parser = SQLParser()
        parsed = parser.parse(
            "SELECT title FROM products WHERE fulltext(title, 'laptop')"
        )

        analyzer = Analyzer(sample_schema)
        result = analyzer.analyze(parsed)

        assert result.get_field_type("title") == "TEXT"

    def test_resolve_numeric_field(self, sample_schema):
        """Analyzer identifies NUMERIC fields."""
        parser = SQLParser()
        parsed = parser.parse("SELECT * FROM products WHERE price > 100")

        analyzer = Analyzer(sample_schema)
        result = analyzer.analyze(parsed)

        assert result.get_field_type("price") == "NUMERIC"

    def test_resolve_tag_field(self, sample_schema):
        """Analyzer identifies TAG fields."""
        parser = SQLParser()
        parsed = parser.parse("SELECT * FROM products WHERE category = 'electronics'")

        analyzer = Analyzer(sample_schema)
        result = analyzer.analyze(parsed)

        assert result.get_field_type("category") == "TAG"

    def test_resolve_vector_field(self, sample_schema):
        """Analyzer identifies VECTOR fields."""
        parser = SQLParser()
        parsed = parser.parse(
            "SELECT id, vector_distance(embedding, :vector) AS sim FROM vectors"
        )

        analyzer = Analyzer(sample_schema)
        result = analyzer.analyze(parsed)

        assert result.get_field_type("embedding") == "VECTOR"

    def test_resolve_geo_field(self, sample_schema):
        """Analyzer identifies GEO fields."""
        parser = SQLParser()
        parsed = parser.parse(
            "SELECT name FROM stores WHERE geo_distance(location, POINT(-122, 37)) < 10"
        )

        analyzer = Analyzer(sample_schema)
        result = analyzer.analyze(parsed)

        assert result.get_field_type("location") == "GEO"

    def test_unknown_field_raises_error(self, sample_schema):
        """Analyzer raises error for unknown fields."""
        parser = SQLParser()
        parsed = parser.parse("SELECT unknown_field FROM products")

        analyzer = Analyzer(sample_schema)

        with pytest.raises(ValueError, match="Unknown field"):
            analyzer.analyze(parsed)

    def test_unknown_index_raises_error(self, sample_schema):
        """Analyzer raises error for unknown index."""
        parser = SQLParser()
        parsed = parser.parse("SELECT * FROM unknown_index")

        analyzer = Analyzer(sample_schema)

        with pytest.raises(ValueError, match="Unknown index"):
            analyzer.analyze(parsed)


class TestAnalyzerConditionClassification:
    """Tests for classifying conditions by field type."""

    def test_classify_text_condition(self, sample_schema):
        """Conditions on TEXT fields are classified correctly."""
        parser = SQLParser()
        parsed = parser.parse("SELECT * FROM products WHERE fulltext(title, 'laptop')")

        analyzer = Analyzer(sample_schema)
        result = analyzer.analyze(parsed)

        text_conditions = result.get_conditions_by_type("TEXT")
        assert len(text_conditions) == 1
        assert text_conditions[0].field == "title"

    def test_classify_numeric_condition(self, sample_schema):
        """Conditions on NUMERIC fields are classified correctly."""
        parser = SQLParser()
        parsed = parser.parse("SELECT * FROM products WHERE price > 100 AND stock > 0")

        analyzer = Analyzer(sample_schema)
        result = analyzer.analyze(parsed)

        numeric_conditions = result.get_conditions_by_type("NUMERIC")
        assert len(numeric_conditions) == 2

    def test_classify_tag_condition(self, sample_schema):
        """Conditions on TAG fields are classified correctly."""
        parser = SQLParser()
        parsed = parser.parse(
            "SELECT * FROM products WHERE category = 'books' AND tags IN ('sale')"
        )

        analyzer = Analyzer(sample_schema)
        result = analyzer.analyze(parsed)

        tag_conditions = result.get_conditions_by_type("TAG")
        assert len(tag_conditions) == 2

    def test_classify_mixed_conditions(self, sample_schema):
        """Mixed field type conditions are classified correctly."""
        parser = SQLParser()
        parsed = parser.parse(
            "SELECT * FROM products "
            "WHERE fulltext(title, 'laptop') AND price < 1000 AND category = 'electronics'"
        )

        analyzer = Analyzer(sample_schema)
        result = analyzer.analyze(parsed)

        assert len(result.get_conditions_by_type("TEXT")) == 1
        assert len(result.get_conditions_by_type("NUMERIC")) == 1
        assert len(result.get_conditions_by_type("TAG")) == 1


class TestAnalyzerAggregations:
    """Tests for analyzing aggregation functions."""

    def test_extract_count_aggregation(self, sample_schema):
        """Analyzer extracts COUNT aggregation."""
        parser = SQLParser()
        parsed = parser.parse("SELECT COUNT(*) AS total FROM products")

        analyzer = Analyzer(sample_schema)
        result = analyzer.analyze(parsed)

        assert len(result.aggregations) == 1
        assert result.aggregations[0].function == "COUNT"
        assert result.aggregations[0].field is None  # COUNT(*)
        assert result.aggregations[0].alias == "total"

    def test_extract_multiple_aggregations(self, sample_schema):
        """Analyzer extracts multiple aggregations."""
        parser = SQLParser()
        parsed = parser.parse(
            "SELECT category, COUNT(*) AS cnt, SUM(price) AS total, AVG(rating) AS avg_rating "
            "FROM products GROUP BY category"
        )

        analyzer = Analyzer(sample_schema)
        result = analyzer.analyze(parsed)

        assert len(result.aggregations) == 3
        funcs = [a.function for a in result.aggregations]
        assert "COUNT" in funcs
        assert "SUM" in funcs
        assert "AVG" in funcs

    def test_extract_groupby_fields(self, sample_schema):
        """Analyzer extracts GROUP BY fields."""
        parser = SQLParser()
        parsed = parser.parse(
            "SELECT category, COUNT(*) FROM products GROUP BY category"
        )

        analyzer = Analyzer(sample_schema)
        result = analyzer.analyze(parsed)

        assert result.groupby_fields == ["category"]

    def test_global_aggregation_no_groupby(self, sample_schema):
        """Analyzer detects global aggregation (no GROUP BY)."""
        parser = SQLParser()
        parsed = parser.parse("SELECT COUNT(*) AS total FROM products")

        analyzer = Analyzer(sample_schema)
        result = analyzer.analyze(parsed)

        assert result.is_global_aggregation is True
        assert result.groupby_fields == []


class TestAnalyzerComputedFields:
    """Tests for analyzing computed/APPLY fields."""

    def test_extract_computed_expression(self, sample_schema):
        """Analyzer extracts computed field expressions."""
        parser = SQLParser()
        parsed = parser.parse("SELECT price, (price * 0.9) AS discounted FROM products")

        analyzer = Analyzer(sample_schema)
        result = analyzer.analyze(parsed)

        assert len(result.computed_fields) == 1
        assert result.computed_fields[0].expression == "price * 0.9"
        assert result.computed_fields[0].alias == "discounted"

    def test_extract_function_expression(self, sample_schema):
        """Analyzer extracts function-based computed fields."""
        parser = SQLParser()
        parsed = parser.parse("SELECT UPPER(name) AS upper_name FROM products")

        analyzer = Analyzer(sample_schema)
        result = analyzer.analyze(parsed)

        assert len(result.computed_fields) == 1
        assert "upper" in result.computed_fields[0].expression.lower()


class TestAnalyzerVectorSearch:
    """Tests for analyzing vector search queries."""

    def test_detect_knn_search(self, sample_schema):
        """Analyzer detects KNN vector search."""
        parser = SQLParser()
        parsed = parser.parse(
            "SELECT id, vector_distance(embedding, :vector) AS sim "
            "FROM vectors ORDER BY sim ASC LIMIT 5"
        )

        analyzer = Analyzer(sample_schema)
        result = analyzer.analyze(parsed)

        assert result.vector_search is not None
        assert result.vector_search.field == "embedding"
        assert result.vector_search.k == 5
        assert result.vector_search.alias == "sim"

    def test_detect_hybrid_search(self, sample_schema):
        """Analyzer detects hybrid (filter + vector) search."""
        parser = SQLParser()
        parsed = parser.parse(
            "SELECT id, vector_distance(embedding, :vector) AS score "
            "FROM vectors WHERE category = 'electronics' "
            "ORDER BY score ASC LIMIT 5"
        )

        analyzer = Analyzer(sample_schema)
        result = analyzer.analyze(parsed)

        assert result.vector_search is not None
        assert result.has_prefilter is True
