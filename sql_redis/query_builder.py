"""RediSearch query builder - generates query syntax from analyzed queries."""

from __future__ import annotations

import re
import warnings

# Redis default stopwords - these are not indexed by default
# See: https://redis.io/docs/latest/develop/ai/search-and-query/advanced-concepts/stopwords/
REDIS_DEFAULT_STOPWORDS = frozenset(
    {
        "a",
        "is",
        "the",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "if",
        "in",
        "into",
        "it",
        "no",
        "not",
        "of",
        "on",
        "or",
        "such",
        "that",
        "their",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "was",
        "will",
        "with",
    }
)


class QueryBuilder:
    """Builds RediSearch query syntax from conditions."""

    # Characters that need escaping in TAG values
    TAG_SPECIAL_CHARS = r".,<>{}[]\"':;!@#$%^&*()-+=~"

    # Characters that have special meaning in RediSearch free-text queries
    # (outside double-quoted phrases). Must be escaped with backslash.
    # Includes double-quote to prevent starting/ending quoted phrases.
    TEXT_QUERY_SPECIAL_CHARS = set('\\|-()"@~!{}[]^$><=;:*+')

    @classmethod
    def _escape_fulltext_term(cls, term: str) -> str:
        """Escape characters that have special meaning in RediSearch free-text queries.

        Applied to individual terms used outside of double-quoted phrases (e.g.,
        in parenthesized FULLTEXT expressions, LIKE, FUZZY) so that user input
        containing RediSearch operator characters does not alter query semantics
        or produce syntax errors.
        """
        result = []
        for char in term:
            if char in cls.TEXT_QUERY_SPECIAL_CHARS:
                result.append(f"\\{char}")
            else:
                result.append(char)
        return "".join(result)

    @staticmethod
    def _escape_text_value(value: str) -> str:
        """Escape characters that are special inside RediSearch double-quoted phrases.

        Backslashes and double quotes must be escaped so they don't break
        the query syntax or alter its meaning.
        """
        # Escape backslashes first (so we don't double-escape the quote escapes),
        # then escape double quotes.
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def build_text_condition(
        self,
        field: str | list[str],
        operator: str,
        value: str,
        negated: bool = False,
        *,
        fuzzy_level: int | None = None,
        slop: int | None = None,
        inorder: bool = False,
    ) -> str:
        """Build query syntax for TEXT field conditions.

        Args:
            field: Field name or list of field names for multi-field search.
            operator: One of =, !=, FULLTEXT, LIKE, FUZZY.
                - = / !=: exact phrase match, value wrapped in double quotes.
                - FULLTEXT: tokenized keyword search with stopword filtering.
                - LIKE: prefix/suffix/infix pattern (SQL % → RediSearch *).
                - FUZZY: Levenshtein fuzzy match.
            value: The search term or pattern.
            negated: If True, prefix with - for negation.
            fuzzy_level: Levenshtein distance for FUZZY (1, 2, or 3). Default 1.
            slop: Maximum distance between terms for proximity search.
            inorder: If True with slop, require terms in order.

        Returns:
            RediSearch query syntax like @field:"exact phrase" or @field:(term1 term2).
        """
        # Derive negation from both the flag and the operator itself,
        # consistent with how build_tag_condition handles != via operator.
        prefix = "-" if negated or operator == "!=" else ""

        # Build search_value based on operator — shared by single- and multi-field paths
        if operator == "LIKE":
            # Escape special chars in the non-wildcard portion, then convert % → *
            # Split on %, escape each segment, rejoin with *
            parts = value.split("%")
            escaped_parts = [self._escape_fulltext_term(p) for p in parts]
            search_value = "*".join(escaped_parts)
        elif operator == "FUZZY":
            # Escape special chars before wrapping with % markers
            escaped = self._escape_fulltext_term(value)
            level = fuzzy_level if fuzzy_level is not None else 1
            if level not in (1, 2, 3):
                raise ValueError(
                    f"Fuzzy level must be 1, 2, or 3 (got {level}). "
                    "RediSearch supports a maximum Levenshtein distance of 3."
                )
            pct = "%" * level
            search_value = f"{pct}{escaped}{pct}"
        elif operator in ("=", "!="):
            # Exact phrase match — always wrap in quotes, preserve stopwords.
            escaped = self._escape_text_value(value)
            search_value = f'"{escaped}"'
        elif re.search(r"\s+[Oo][Rr]\s+", value):
            # OR union within text field: split on case-insensitive OR with
            # flexible whitespace, escape each term, join with |.
            # Multi-word operands (e.g. "gaming laptop OR tablet") are wrapped
            # in parentheses so each side is an atomic subexpression.
            or_parts: list[str] = []
            for part in re.split(r"\s+[Oo][Rr]\s+", value):
                words = part.strip().split()
                if len(words) > 1:
                    escaped = " ".join(self._escape_fulltext_term(w) for w in words)
                    or_parts.append(f"({escaped})")
                else:
                    or_parts.append(self._escape_fulltext_term(words[0]))
            search_value = f"({'|'.join(or_parts)})"
        elif " " in value:
            # FULLTEXT/MATCH with multi-word: tokenized search with stopword filtering.
            # Each term is escaped to prevent accidental operator injection, but a
            # leading ~ (optional-term modifier) is preserved as an intentional
            # RediSearch operator.
            words = value.split()
            removed_stopwords = [
                w for w in words if w.lower() in REDIS_DEFAULT_STOPWORDS
            ]
            filtered_words = [
                w for w in words if w.lower() not in REDIS_DEFAULT_STOPWORDS
            ]

            if removed_stopwords:
                warnings.warn(
                    f"Stopwords {removed_stopwords} were removed from text search '{value}'. "
                    "By default, Redis does not index stopwords. "
                    "To include stopwords in your index, create it with STOPWORDS 0. "
                    "Use = operator for exact phrase matching that preserves stopwords.",
                    UserWarning,
                    stacklevel=2,
                )

            escaped_words = []
            for w in (filtered_words if filtered_words else words):
                if w.startswith("~"):
                    # Preserve ~ optional-term prefix, escape the rest
                    escaped_words.append("~" + self._escape_fulltext_term(w[1:]))
                else:
                    escaped_words.append(self._escape_fulltext_term(w))

            terms = " ".join(escaped_words)
            search_value = f"({terms})"
        else:
            # Single-word FULLTEXT — escape to prevent accidental operator injection.
            # Preserve ~ optional-term prefix (same as multi-word branch).
            if value.startswith("~"):
                search_value = "~" + self._escape_fulltext_term(value[1:])
            else:
                search_value = self._escape_fulltext_term(value)

        # Handle multi-field search — use computed search_value with multi-field syntax
        if isinstance(field, list):
            field_str = "|".join(field)
            base = f"{prefix}(@{field_str}:{search_value})"
        else:
            base = f"{prefix}@{field}:{search_value}"

        # Append query attributes (slop, inorder) if specified
        if slop is not None:
            attrs = f"$slop: {slop};"
            if inorder:
                attrs += " $inorder: true;"
            base = f"{base} => {{ {attrs} }}"

        return base

    def _escape_tag_value(self, value: str) -> str:
        """Escape special characters in TAG values."""
        result = []
        for char in value:
            if char in self.TAG_SPECIAL_CHARS:
                result.append(f"\\{char}")
            else:
                result.append(char)
        return "".join(result)

    def build_tag_condition(
        self,
        field: str,
        operator: str,
        value: str | list[str],
    ) -> str:
        """Build query syntax for TAG field conditions.

        Args:
            field: Field name.
            operator: One of =, !=, IN.
            value: Tag value or list of values for IN.

        Returns:
            RediSearch query syntax like @field:{value} or @field:{v1|v2}.
        """
        prefix = "-" if operator == "!=" else ""

        if isinstance(value, list):
            # IN clause - join with |
            escaped_values = [self._escape_tag_value(v) for v in value]
            tag_str = "|".join(escaped_values)
        else:
            tag_str = self._escape_tag_value(value)

        return f"{prefix}@{field}:{{{tag_str}}}"

    def build_numeric_condition(
        self,
        field: str,
        operator: str,
        value: int | float | tuple[int | float, int | float],
    ) -> str:
        """Build query syntax for NUMERIC field conditions.

        Args:
            field: Field name.
            operator: One of =, !=, <, <=, >, >=, BETWEEN.
            value: Numeric value or (min, max) tuple for BETWEEN.

        Returns:
            RediSearch query syntax like @field:[min max].
        """
        prefix = "-" if operator == "!=" else ""

        if operator == "BETWEEN":
            if isinstance(value, tuple):
                min_val, max_val = value
                return f"{prefix}@{field}:[{min_val} {max_val}]"
            raise ValueError("BETWEEN operator requires a tuple (min, max)")
        elif operator == "=":
            return f"@{field}:[{value} {value}]"
        elif operator == "!=":
            return f"-@{field}:[{value} {value}]"
        elif operator == ">":
            return f"@{field}:[({value} +inf]"
        elif operator == ">=":
            return f"@{field}:[{value} +inf]"
        elif operator == "<":
            return f"@{field}:[-inf ({value}]"
        elif operator == "<=":
            return f"@{field}:[-inf {value}]"
        else:
            raise ValueError(f"Unknown numeric operator: {operator}")

    def build_vector_condition(
        self,
        field: str,
        k: int,
        alias: str,
        prefilter: str | None = None,
    ) -> str:
        """Build query syntax for VECTOR KNN search.

        Args:
            field: Vector field name.
            k: Number of nearest neighbors.
            alias: Alias for the distance score.
            prefilter: Optional pre-filter query string.

        Returns:
            RediSearch query syntax like =>[KNN k @field $BLOB AS alias].
        """
        knn_part = f"=>[KNN {k} @{field} $BLOB AS {alias}]"
        if prefilter:
            return f"({prefilter}){knn_part}"
        return knn_part

    def build_geo_filter(
        self,
        field: str,
        lon: float,
        lat: float,
        radius: float,
        unit: str = "km",
    ) -> str:
        """Build GEOFILTER clause for GEO fields.

        Args:
            field: GEO field name.
            lon: Longitude.
            lat: Latitude.
            radius: Search radius.
            unit: Distance unit (km, m, mi, ft).

        Returns:
            GEOFILTER clause like "GEOFILTER field lon lat radius unit".
        """
        return f"GEOFILTER {field} {lon} {lat} {radius} {unit}"

    def build_geo_distance_apply(
        self,
        field: str,
        lon: float,
        lat: float,
        alias: str,
        unit: str = "m",
    ) -> tuple[str, str]:
        """Build geodistance expression and alias for APPLY.

        Args:
            field: GEO field name.
            lon: Longitude.
            lat: Latitude.
            alias: Alias for the distance result.
            unit: Distance unit for conversion.

        Returns:
            Tuple of (expression, alias) for use in APPLY clause.
        """
        base_expr = f"geodistance(@{field}, {lon}, {lat})"

        # geodistance returns meters - convert if needed
        # Use consistent conversion factors (same as translator._convert_to_meters)
        if unit == "km":
            expr = f"({base_expr}/1000)"
        elif unit == "mi":
            # 1 mile = 1609.344 meters (consistent with translator)
            expr = f"({base_expr}/1609.344)"
        elif unit == "ft":
            # 1 foot = 0.3048 meters, so meters * (1/0.3048) = meters * 3.28084
            expr = f"({base_expr}*3.28084)"
        else:
            expr = base_expr

        return (expr, alias)

    def combine_conditions(
        self,
        conditions: list[str],
        operator: str = "AND",
    ) -> str:
        """Combine multiple condition strings with boolean operator.

        Args:
            conditions: List of query condition strings.
            operator: Boolean operator (AND, OR).

        Returns:
            Combined query string.
        """
        if not conditions:
            return "*"
        if len(conditions) == 1:
            return conditions[0]

        if operator == "OR":
            # OR uses pipe separator - each condition needs parentheses
            parenthesized = [
                f"({c})" if not c.startswith("(") else c for c in conditions
            ]
            return "(" + "|".join(parenthesized) + ")"
        else:
            # AND uses space separator
            return " ".join(conditions)

    def build_query_string(
        self,
        text_conditions: list[tuple] | None = None,
        numeric_conditions: list[tuple] | None = None,
        tag_conditions: list[tuple] | None = None,
        field_types: dict[str, str] | None = None,
    ) -> str:
        """Build complete query string from conditions.

        Args:
            text_conditions: List of (field, operator, value) tuples.
            numeric_conditions: List of (field, operator, value) tuples.
            tag_conditions: List of (field, operator, value) tuples.
            field_types: Dict mapping field names to types.

        Returns:
            Complete RediSearch query string.
        """
        parts = []

        # Build text conditions
        if text_conditions:
            for field, operator, value in text_conditions:
                parts.append(self.build_text_condition(field, operator, value))

        # Build numeric conditions
        if numeric_conditions:
            for field, operator, value in numeric_conditions:
                parts.append(self.build_numeric_condition(field, operator, value))

        # Build tag conditions
        if tag_conditions:
            for field, operator, value in tag_conditions:
                parts.append(self.build_tag_condition(field, operator, value))

        return self.combine_conditions(parts, "AND")

    def build_missing_condition(self, field: str, *, is_missing: bool) -> str:
        """Build ismissing() query fragment for IS NULL / IS NOT NULL.

        Args:
            field: Field name (without @ prefix).
            is_missing: True for IS NULL (ismissing), False for IS NOT NULL (-ismissing).

        Returns:
            Query fragment like "ismissing(@field)" or "-ismissing(@field)".
        """
        if is_missing:
            return f"ismissing(@{field})"
        return f"-ismissing(@{field})"
