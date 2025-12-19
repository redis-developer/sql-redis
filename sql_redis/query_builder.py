"""RediSearch query builder - generates query syntax from analyzed queries."""


class QueryBuilder:
    """Builds RediSearch query syntax from conditions."""
    
    def build_text_condition(
        self, 
        field: str | list[str], 
        operator: str, 
        value: str,
        negated: bool = False
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
        raise NotImplementedError("build_text_condition is not yet implemented")
    
    def build_tag_condition(
        self,
        field: str,
        operator: str,
        value: str | list[str]
    ) -> str:
        """Build query syntax for TAG field conditions.
        
        Args:
            field: Field name.
            operator: One of =, !=, IN.
            value: Tag value or list of values for IN.
            
        Returns:
            RediSearch query syntax like @field:{value} or @field:{v1|v2}.
        """
        raise NotImplementedError("build_tag_condition is not yet implemented")
    
    def build_numeric_condition(
        self,
        field: str,
        operator: str,
        value: int | float | tuple[int | float, int | float]
    ) -> str:
        """Build query syntax for NUMERIC field conditions.
        
        Args:
            field: Field name.
            operator: One of =, !=, <, <=, >, >=, BETWEEN.
            value: Numeric value or (min, max) tuple for BETWEEN.
            
        Returns:
            RediSearch query syntax like @field:[min max].
        """
        raise NotImplementedError("build_numeric_condition is not yet implemented")
    
    def build_vector_condition(
        self,
        field: str,
        k: int,
        alias: str,
        prefilter: str | None = None
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
        raise NotImplementedError("build_vector_condition is not yet implemented")
    
    def build_geo_filter(
        self,
        field: str,
        lon: float,
        lat: float,
        radius: float,
        unit: str = "km"
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
        raise NotImplementedError("build_geo_filter is not yet implemented")
    
    def build_geo_distance_apply(
        self,
        field: str,
        lon: float,
        lat: float,
        alias: str,
        unit: str = "m"
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
        raise NotImplementedError("build_geo_distance_apply is not yet implemented")
    
    def combine_conditions(
        self,
        conditions: list[str],
        operator: str = "AND"
    ) -> str:
        """Combine multiple condition strings with boolean operator.
        
        Args:
            conditions: List of query condition strings.
            operator: Boolean operator (AND, OR).
            
        Returns:
            Combined query string.
        """
        raise NotImplementedError("combine_conditions is not yet implemented")
    
    def build_query_string(
        self,
        text_conditions: list[tuple] | None = None,
        numeric_conditions: list[tuple] | None = None,
        tag_conditions: list[tuple] | None = None,
        field_types: dict[str, str] | None = None
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
        raise NotImplementedError("build_query_string is not yet implemented")

