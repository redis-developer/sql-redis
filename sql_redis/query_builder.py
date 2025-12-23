"""RediSearch query builder - generates query syntax from analyzed queries."""


class QueryBuilder:
    """Builds RediSearch query syntax from conditions."""

    # Characters that need escaping in TAG values
    TAG_SPECIAL_CHARS = r".,<>{}[]\"':;!@#$%^&*()-+=~"

    def build_text_condition(
        self,
        field: str | list[str],
        operator: str,
        value: str,
        negated: bool = False,
    ) -> str:
        """Build query syntax for TEXT field conditions.

        Args:
            field: Field name or list of field names for multi-field search.
            operator: One of =, MATCH, LIKE, FUZZY.
            value: The search term or pattern.
            negated: If True, prefix with - for negation.

        Returns:
            RediSearch query syntax like @field:term or @field:"phrase".
        """
        prefix = "-" if negated else ""

        # Handle multi-field search
        if isinstance(field, list):
            field_str = "|".join(field)
            return f"(@{field_str}:{value})"

        # Handle different operators
        if operator == "LIKE":
            # Convert SQL LIKE pattern (%) to RediSearch prefix (*)
            search_value = value.replace("%", "*")
        elif operator == "FUZZY":
            # Wrap with % for fuzzy matching
            search_value = f"%{value}%"
        elif " " in value:
            # Phrase search - wrap in quotes
            search_value = f'"{value}"'
        else:
            search_value = value

        return f"{prefix}@{field}:{search_value}"
    
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
            min_val, max_val = value
            return f"{prefix}@{field}:[{min_val} {max_val}]"
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
    ) -> str:
        """Build APPLY geodistance expression.

        Args:
            field: GEO field name.
            lon: Longitude.
            lat: Latitude.
            alias: Alias for the distance result.
            unit: Distance unit for conversion.

        Returns:
            APPLY clause like 'APPLY "geodistance(@field, lon, lat)" AS alias'.
        """
        base_expr = f"geodistance(@{field}, {lon}, {lat})"

        # geodistance returns meters - convert if needed
        if unit == "km":
            expr = f"({base_expr}/1000)"
        elif unit == "mi":
            expr = f"({base_expr}/1609.34)"
        elif unit == "ft":
            expr = f"({base_expr}*3.28084)"
        else:
            expr = base_expr

        return f'APPLY "{expr}" AS {alias}'
    
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
            parenthesized = [f"({c})" if not c.startswith("(") else c for c in conditions]
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

