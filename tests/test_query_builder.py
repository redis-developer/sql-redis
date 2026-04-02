"""Tests for the RediSearch query builder component."""

import pytest

from sql_redis.query_builder import QueryBuilder


class TestQueryBuilderTextFields:
    """Tests for building TEXT field query syntax."""

    def test_text_single_term_exact(self):
        """TEXT field with = wraps in quotes for exact phrase: @field:"term"."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "=", "laptop")

        assert result == '@title:"laptop"'

    def test_text_exact_phrase(self):
        """TEXT field with = preserves multi-word phrase: @field:"exact phrase"."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "=", "gaming laptop")

        assert result == '@title:"gaming laptop"'

    def test_text_exact_phrase_preserves_stopwords(self):
        """TEXT field with = preserves stopwords in exact phrase matching."""
        builder = QueryBuilder()
        result = builder.build_text_condition("name", "=", "bank of america")

        # Stopwords like "of" must NOT be stripped for exact phrase matching
        assert result == '@name:"bank of america"'

    def test_text_exact_phrase_escapes_quotes(self):
        """TEXT field with = escapes double quotes inside the value."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "=", 'say "hello"')

        assert result == r'@title:"say \"hello\""'

    def test_text_exact_phrase_escapes_backslashes(self):
        """TEXT field with = escapes backslashes inside the value."""
        builder = QueryBuilder()
        result = builder.build_text_condition("path", "=", r"c:\users\docs")

        assert result == r'@path:"c:\\users\\docs"'

    def test_text_fulltext_term(self):
        """TEXT field with FULLTEXT (tokenized search): @field:term."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "FULLTEXT", "laptop")

        assert result == "@title:laptop"

    def test_text_fulltext_multi_word(self):
        """TEXT field with FULLTEXT and multi-word: @field:(term1 term2)."""
        builder = QueryBuilder()
        result = builder.build_text_condition(
            "description", "FULLTEXT", "gaming laptop"
        )

        assert result == "@description:(gaming laptop)"

    def test_text_prefix_search(self):
        """TEXT field with prefix: @field:prefix*."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "LIKE", "lap%")

        assert result == "@title:lap*"

    def test_text_negation(self):
        """TEXT field with NOT: -@field:term."""
        builder = QueryBuilder()
        result = builder.build_text_condition(
            "title", "FULLTEXT", "refurbished", negated=True
        )

        assert result == "-@title:refurbished"

    def test_text_fuzzy_match(self):
        """TEXT field with fuzzy: @field:%term%."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "FUZZY", "laptap")

        assert result == "@title:%laptap%"

    def test_text_fulltext_special_chars_escaped(self):
        """FULLTEXT term with RediSearch operator chars is escaped to avoid injection."""
        builder = QueryBuilder()
        result = builder.build_text_condition("description", "FULLTEXT", "anti-virus")

        assert result == r"@description:anti\-virus"

    def test_text_multi_field(self):
        """TEXT multi-field search: (@field1|field2:term)."""
        builder = QueryBuilder()
        result = builder.build_text_condition(
            ["title", "description"], "FULLTEXT", "wireless"
        )

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
        result = builder.build_geo_filter(
            "location", lon=-122.4, lat=37.8, radius=10, unit="km"
        )

        assert result == "GEOFILTER location -122.4 37.8 10 km"

    def test_geo_distance_apply(self):
        """GEO field distance as computed field."""
        builder = QueryBuilder()
        expr, alias = builder.build_geo_distance_apply(
            "location", lon=-122.4, lat=37.8, alias="dist"
        )

        assert expr == "geodistance(@location, -122.4, 37.8)"
        assert alias == "dist"

    def test_geo_distance_in_miles(self):
        """GEO field distance converted to miles."""
        builder = QueryBuilder()
        expr, alias = builder.build_geo_distance_apply(
            "location", lon=-73.99, lat=40.75, alias="miles", unit="mi"
        )

        # geodistance returns meters, divide by 1609.344 for miles
        assert "1609.344" in expr
        assert alias == "miles"

    def test_geo_distance_in_km(self):
        """GEO field distance converted to kilometers."""
        builder = QueryBuilder()
        expr, alias = builder.build_geo_distance_apply(
            "location", lon=-122.4, lat=37.8, alias="km_dist", unit="km"
        )

        assert "/1000" in expr
        assert alias == "km_dist"

    def test_geo_distance_in_feet(self):
        """GEO field distance converted to feet."""
        builder = QueryBuilder()
        expr, alias = builder.build_geo_distance_apply(
            "location", lon=-122.4, lat=37.8, alias="ft_dist", unit="ft"
        )

        assert "*3.28084" in expr
        assert alias == "ft_dist"


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
            text_conditions=[("title", "FULLTEXT", "laptop")],
            field_types={"title": "TEXT"},
        )

        assert result == "@title:laptop"

    def test_combined_query(self):
        """Build combined text + numeric + tag query."""
        builder = QueryBuilder()
        result = builder.build_query_string(
            text_conditions=[("title", "FULLTEXT", "laptop")],
            numeric_conditions=[("price", "<", 1000)],
            tag_conditions=[("category", "=", "electronics")],
            field_types={"title": "TEXT", "price": "NUMERIC", "category": "TAG"},
        )

        assert "@title:laptop" in result
        assert "@price:" in result
        assert "@category:{electronics}" in result

    def test_wildcard_query(self):
        """Build wildcard query for no conditions."""
        builder = QueryBuilder()
        result = builder.build_query_string(field_types={})

        assert result == "*"


class TestQueryBuilderFuzzyLevels:
    """Tests for fuzzy matching with Levenshtein distance levels 1-3."""

    def test_fuzzy_ld1_default(self):
        """Fuzzy LD=1 (default): @field:%term%."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "FUZZY", "laptap")
        assert result == "@title:%laptap%"

    def test_fuzzy_ld1_explicit(self):
        """Fuzzy LD=1 (explicit): @field:%term%."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "FUZZY", "laptap", fuzzy_level=1)
        assert result == "@title:%laptap%"

    def test_fuzzy_ld2(self):
        """Fuzzy LD=2: @field:%%term%%."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "FUZZY", "laptap", fuzzy_level=2)
        assert result == "@title:%%laptap%%"

    def test_fuzzy_ld3(self):
        """Fuzzy LD=3: @field:%%%term%%%."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "FUZZY", "laptap", fuzzy_level=3)
        assert result == "@title:%%%laptap%%%"

    def test_fuzzy_negated(self):
        """Fuzzy with negation: -@field:%term%."""
        builder = QueryBuilder()
        result = builder.build_text_condition(
            "title", "FUZZY", "laptap", negated=True, fuzzy_level=2
        )
        assert result == "-@title:%%laptap%%"

    def test_fuzzy_invalid_level_raises(self):
        """Fuzzy level outside 1-3 raises ValueError."""
        builder = QueryBuilder()
        with pytest.raises(ValueError, match="Fuzzy level must be 1, 2, or 3"):
            builder.build_text_condition("title", "FUZZY", "laptap", fuzzy_level=4)

    def test_fuzzy_level_zero_raises(self):
        """Fuzzy level 0 raises ValueError."""
        builder = QueryBuilder()
        with pytest.raises(ValueError, match="Fuzzy level must be 1, 2, or 3"):
            builder.build_text_condition("title", "FUZZY", "laptap", fuzzy_level=0)


class TestQueryBuilderSuffixInfix:
    """Tests for suffix and infix (contains) matching."""

    def test_suffix_match(self):
        """LIKE '%term' -> suffix match: @field:*term."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "LIKE", "%phone")
        assert result == "@title:*phone"

    def test_infix_match(self):
        """LIKE '%term%' -> infix/contains match: @field:*term*."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "LIKE", "%phone%")
        assert result == "@title:*phone*"

    def test_prefix_match_still_works(self):
        """LIKE 'term%' -> prefix match: @field:term* (unchanged)."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "LIKE", "lap%")
        assert result == "@title:lap*"

    def test_suffix_negated(self):
        """Suffix match with negation."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "LIKE", "%phone", negated=True)
        assert result == "-@title:*phone"

    def test_infix_multiword_grouped(self):
        """LIKE '%multi word%' groups tokens in parentheses."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "LIKE", "%gaming laptop%")
        assert result == "@title:(*gaming laptop*)"


class TestQueryBuilderORInText:
    """Tests for OR/union within text field searches."""

    def test_fulltext_or_terms(self):
        """FULLTEXT with OR: @field:(term1|term2)."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "FULLTEXT", "laptop OR tablet")
        assert result == "@title:(laptop|tablet)"

    def test_fulltext_or_multiple_terms(self):
        """FULLTEXT with multiple OR: @field:(t1|t2|t3)."""
        builder = QueryBuilder()
        result = builder.build_text_condition(
            "title", "FULLTEXT", "laptop OR tablet OR phone"
        )
        assert result == "@title:(laptop|tablet|phone)"

    def test_fulltext_or_negated(self):
        """FULLTEXT OR with negation: -@field:(term1|term2)."""
        builder = QueryBuilder()
        result = builder.build_text_condition(
            "title", "FULLTEXT", "laptop OR tablet", negated=True
        )
        assert result == "-@title:(laptop|tablet)"

    def test_fulltext_and_still_works(self):
        """FULLTEXT without OR: @field:(term1 term2) (AND semantics, unchanged)."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "FULLTEXT", "gaming laptop")
        assert result == "@title:(gaming laptop)"


class TestQueryBuilderProximity:
    """Tests for proximity search (slop and inorder)."""

    def test_fulltext_with_slop(self):
        """FULLTEXT with slop: @field:(term1 term2) => {$slop: N}."""
        builder = QueryBuilder()
        result = builder.build_text_condition(
            "title", "FULLTEXT", "gaming laptop", slop=2
        )
        assert result == "@title:(gaming laptop) => { $slop: 2; }"

    def test_fulltext_with_slop_and_inorder(self):
        """FULLTEXT with slop and inorder."""
        builder = QueryBuilder()
        result = builder.build_text_condition(
            "title", "FULLTEXT", "gaming laptop", slop=2, inorder=True
        )
        assert result == "@title:(gaming laptop) => { $slop: 2; $inorder: true; }"

    def test_exact_phrase_with_slop(self):
        """Exact phrase with slop appended."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "=", "gaming laptop", slop=1)
        assert result == '@title:"gaming laptop" => { $slop: 1; }'

    def test_slop_negated(self):
        """Proximity with negation."""
        builder = QueryBuilder()
        result = builder.build_text_condition(
            "title", "FULLTEXT", "gaming laptop", negated=True, slop=3
        )
        assert result == "-@title:(gaming laptop) => { $slop: 3; }"


class TestQueryBuilderOptionalTerms:
    """Tests for optional term (~) syntax."""

    def test_fulltext_optional_term(self):
        """FULLTEXT with optional terms using ~ prefix in value."""
        builder = QueryBuilder()
        # User writes: fulltext(field, 'required ~optional')
        result = builder.build_text_condition("title", "FULLTEXT", "laptop ~gaming")
        assert result == "@title:(laptop ~gaming)"


class TestQueryBuilderMissingCondition:
    """Tests for ismissing() query syntax."""

    def test_build_missing_condition_is_null(self):
        """IS NULL produces ismissing(@field)."""
        builder = QueryBuilder()
        result = builder.build_missing_condition("email", is_missing=True)
        assert result == "ismissing(@email)"

    def test_build_missing_condition_is_not_null(self):
        """IS NOT NULL produces -ismissing(@field)."""
        builder = QueryBuilder()
        result = builder.build_missing_condition("email", is_missing=False)
        assert result == "-ismissing(@email)"


class TestQueryBuilderEscaping:
    """Tests for escaping special characters in text search values."""

    def test_escape_fulltext_term_special_chars(self):
        """_escape_fulltext_term escapes RediSearch operator characters."""
        builder = QueryBuilder()
        result = builder._escape_fulltext_term("hello|world")
        assert result == "hello\\|world"

    def test_escape_fulltext_term_double_quote(self):
        """_escape_fulltext_term escapes double quotes."""
        builder = QueryBuilder()
        result = builder._escape_fulltext_term('say "hello"')
        assert result == 'say \\"hello\\"'

    def test_escape_fulltext_term_at_sign(self):
        """_escape_fulltext_term escapes @ to prevent field injection."""
        builder = QueryBuilder()
        result = builder._escape_fulltext_term("user@email")
        assert result == "user\\@email"

    def test_like_escapes_special_chars(self):
        """LIKE pattern escapes special chars in the non-wildcard portion."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "LIKE", "%hello|world%")
        assert result == "@title:*hello\\|world*"

    def test_fuzzy_escapes_special_chars(self):
        """FUZZY escapes special chars in the term before wrapping with %."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "FUZZY", "hello@world")
        assert result == "@title:%hello\\@world%"

    def test_fuzzy_escapes_double_quote(self):
        """FUZZY escapes double quotes in term."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "FUZZY", 'say"hi')
        assert result == '@title:%say\\"hi%'

    def test_multi_field_non_exact_escapes(self):
        """Multi-field search with non-exact operator escapes special chars."""
        builder = QueryBuilder()
        result = builder.build_text_condition(
            ["title", "description"], "FULLTEXT", "hello|world"
        )
        assert result == "(@title|description:hello\\|world)"

    def test_or_terms_escape_special_chars(self):
        """OR operands are escaped before joining with |."""
        builder = QueryBuilder()
        result = builder.build_text_condition(
            "title", "FULLTEXT", "laptop OR anti-virus"
        )
        # '-' is a RediSearch operator and should be escaped in OR terms
        assert result == "@title:(laptop|anti\\-virus)"

    def test_or_terms_escape_at_sign(self):
        """OR operands escape @ to prevent field injection."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "FULLTEXT", "hello OR @world")
        assert result == "@title:(hello|\\@world)"

    def test_multiword_fulltext_escapes_special_chars(self):
        """Multi-word FULLTEXT escapes dangerous chars like @ and |."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "FULLTEXT", "hello @world")
        assert result == "@title:(hello \\@world)"

    def test_multiword_fulltext_preserves_optional_prefix(self):
        """Multi-word FULLTEXT preserves ~ optional-term prefix."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "FULLTEXT", "laptop ~gaming")
        assert result == "@title:(laptop ~gaming)"

    def test_multiword_fulltext_escapes_dash(self):
        """Multi-word FULLTEXT escapes - to prevent accidental negation."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "FULLTEXT", "hello anti-virus")
        assert result == "@title:(hello anti\\-virus)"

    def test_multi_field_multiword_fulltext(self):
        """Multi-field with multi-word FULLTEXT scopes all terms to fields."""
        builder = QueryBuilder()
        result = builder.build_text_condition(
            ["title", "description"], "FULLTEXT", "gaming laptop"
        )
        assert result == "(@title|description:(gaming laptop))"

    def test_multi_field_or_fulltext(self):
        """Multi-field with OR FULLTEXT uses pipe-separated terms."""
        builder = QueryBuilder()
        result = builder.build_text_condition(
            ["title", "description"], "FULLTEXT", "laptop OR tablet"
        )
        assert result == "(@title|description:(laptop|tablet))"

    def test_multi_field_fuzzy(self):
        """Multi-field with FUZZY wraps with % markers."""
        builder = QueryBuilder()
        result = builder.build_text_condition(
            ["title", "description"], "FUZZY", "laptap", fuzzy_level=2
        )
        assert result == "(@title|description:%%laptap%%)"

    def test_lowercase_or_is_not_boolean(self):
        """Lowercase 'or' is treated as a regular search term, not a boolean operator.

        'bank or america' should NOT become bank|america — it should be a
        multi-word AND-style search with stopword filtering applied to 'or'.
        """
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "FULLTEXT", "laptop or tablet")
        # "or" is a stopword; remaining terms are "laptop" and "tablet"
        assert result == "@title:(laptop tablet)"

    def test_mixed_case_or_is_not_boolean(self):
        """Mixed case 'Or' / 'oR' is treated as a regular term, not boolean OR.

        Only uppercase 'OR' triggers the union operator.
        'Or' is a stopword (or → stopword list), so it gets removed.
        """
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "FULLTEXT", "laptop Or tablet")
        # "Or" lowercases to "or" which is a stopword; remaining: laptop tablet
        assert result == "@title:(laptop tablet)"

    def test_or_extra_whitespace(self):
        """OR parsing tolerates extra whitespace."""
        builder = QueryBuilder()
        result = builder.build_text_condition(
            "title", "FULLTEXT", "laptop  OR   tablet"
        )
        assert result == "@title:(laptop|tablet)"

    def test_escape_asterisk_in_fulltext(self):
        """Literal * in FULLTEXT is escaped to prevent wildcard."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "FULLTEXT", "hello*world")
        assert result == r"@title:hello\*world"

    def test_escape_plus_in_fulltext(self):
        """Literal + in FULLTEXT is escaped to prevent mandatory-term."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "FULLTEXT", "C++")
        assert result == r"@title:C\+\+"

    def test_single_term_optional_prefix_preserved(self):
        """Single-term FULLTEXT with ~ prefix preserves optional semantics."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "FULLTEXT", "~gaming")
        assert result == "@title:~gaming"

    def test_or_multiword_operand_grouped(self):
        """OR with multi-word operand wraps it in parentheses."""
        builder = QueryBuilder()
        result = builder.build_text_condition(
            "title", "FULLTEXT", "gaming laptop OR tablet"
        )
        assert result == "@title:((gaming laptop)|tablet)"

    def test_or_both_multiword_operands_grouped(self):
        """OR with multi-word operands on both sides wraps each."""
        builder = QueryBuilder()
        result = builder.build_text_condition(
            "title", "FULLTEXT", "gaming laptop OR android tablet"
        )
        assert result == "@title:((gaming laptop)|(android tablet))"

    def test_or_trailing_empty_operand_raises(self):
        """Trailing OR with empty operand raises ValueError."""
        builder = QueryBuilder()
        with pytest.raises(ValueError, match="Empty operand in OR expression"):
            builder.build_text_condition("title", "FULLTEXT", "laptop OR ")

    def test_or_leading_empty_operand_raises(self):
        """Leading OR with empty operand raises ValueError."""
        builder = QueryBuilder()
        with pytest.raises(ValueError, match="Empty operand in OR expression"):
            builder.build_text_condition("title", "FULLTEXT", " OR tablet")

    def test_or_preserves_optional_prefix(self):
        """OR operand with ~ prefix preserves optional-term semantics."""
        builder = QueryBuilder()
        result = builder.build_text_condition("title", "FULLTEXT", "laptop OR ~gaming")
        assert result == "@title:(laptop|~gaming)"


class TestQueryBuilderSlopValidation:
    """Tests for slop validation at the QueryBuilder level."""

    def test_slop_negative_raises(self):
        """Negative slop raises ValueError."""
        builder = QueryBuilder()
        with pytest.raises(ValueError, match="non-negative integer"):
            builder.build_text_condition("title", "FULLTEXT", "gaming laptop", slop=-1)

    def test_slop_boolean_raises(self):
        """Boolean slop raises ValueError."""
        builder = QueryBuilder()
        with pytest.raises(ValueError, match="non-negative integer"):
            builder.build_text_condition(
                "title", "FULLTEXT", "gaming laptop", slop=True
            )
