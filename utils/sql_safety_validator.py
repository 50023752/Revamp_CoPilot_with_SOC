"""
SQL Safety Validator
Validates SQL queries to prevent destructive operations
"""

import re
from typing import Tuple, List, Optional


class SQLSafetyValidator:
    """
    Validates SQL queries to ensure they are read-only and safe to execute.
    Blocks destructive operations like DELETE, UPDATE, TRUNCATE, DROP, ALTER.
    """
    
    # Destructive SQL keywords that should be blocked
    BLOCKED_KEYWORDS = [
        r'\bDELETE\b',
        r'\bUPDATE\b',
        r'\bTRUNCATE\b',
        r'\bDROP\b',
        r'\bALTER\b',
        r'\bINSERT\b',
        r'\bMERGE\b',
        r'\bCREATE\b',
        r'\bREPLACE\b',
        r'\bGRANT\b',
        r'\bREVOKE\b',
    ]
    
    # Allowed operations (whitelist approach)
    ALLOWED_KEYWORDS = [
        r'\bSELECT\b',
        r'\bWITH\b',  # For CTEs
    ]
    
    @classmethod
    def validate(cls, sql_query: str) -> Tuple[bool, Optional[str]]:
        """
        Validate SQL query for safety.
        
        Args:
            sql_query: SQL query string to validate
            
        Returns:
            Tuple of (is_valid, error_message)
            - is_valid: True if query is safe, False otherwise
            - error_message: Description of why query was blocked (None if valid)
        """
        if not sql_query or not sql_query.strip():
            return False, "SQL query is empty"
        
        # Normalize query for pattern matching
        normalized_query = sql_query.upper()
        
        # Check for semicolon-separated statements FIRST (potential SQL injection)
        if cls._contains_multiple_statements(sql_query):
            return False, "Blocked: Query contains multiple statements (potential SQL injection)"
        
        # Check for SQL comments that might hide malicious code
        if cls._contains_suspicious_comments(sql_query):
            return False, "Blocked: Query contains suspicious SQL comments"
        
        # Check for blocked keywords
        for blocked_pattern in cls.BLOCKED_KEYWORDS:
            if re.search(blocked_pattern, normalized_query, re.IGNORECASE):
                keyword = blocked_pattern.replace(r'\b', '').replace('\\', '')
                return False, f"Blocked: Query contains destructive keyword '{keyword}'"
        
        # Verify query starts with allowed keywords
        if not cls._starts_with_allowed_keyword(normalized_query):
            return False, "Blocked: Query must start with SELECT or WITH"
        
        return True, None
    
    @classmethod
    def _contains_suspicious_comments(cls, sql_query: str) -> bool:
        """Check for suspicious SQL comments that might hide code"""
        # Block inline comments with semicolons (could hide statements)
        if re.search(r'--[^\\n]*;', sql_query):
            return True
        # Block multi-line comments with semicolons
        if re.search(r'/\*.*?;.*?\*/', sql_query, re.DOTALL):
            return True
        return False
    
    @classmethod
    def _contains_multiple_statements(cls, sql_query: str) -> bool:
        """Check if query contains multiple statements"""
        # Remove string literals to avoid false positives
        cleaned = re.sub(r"'[^']*'", '', sql_query)
        cleaned = re.sub(r'"[^"]*"', '', cleaned)
        
        # Count semicolons (allowing one at the end)
        semicolons = [m.start() for m in re.finditer(';', cleaned)]
        
        # Allow one trailing semicolon
        if len(semicolons) == 0:
            return False
        if len(semicolons) == 1 and semicolons[0] == len(cleaned.strip()) - 1:
            return False
        
        return True
    
    @classmethod
    def _starts_with_allowed_keyword(cls, normalized_query: str) -> bool:
        """Check if query starts with an allowed keyword"""
        normalized_query = normalized_query.strip()
        for allowed_pattern in cls.ALLOWED_KEYWORDS:
            if re.match(allowed_pattern, normalized_query, re.IGNORECASE):
                return True
        return False
    
    @classmethod
    def get_blocked_patterns(cls) -> List[str]:
        """Return list of blocked SQL patterns for documentation"""
        return [pattern.replace(r'\b', '').replace('\\', '') for pattern in cls.BLOCKED_KEYWORDS]


# Example usage and testing
if __name__ == "__main__":
    # Test cases
    test_queries = [
        ("SELECT * FROM table", True, None),
        ("WITH cte AS (SELECT * FROM t1) SELECT * FROM cte", True, None),
        ("DELETE FROM table WHERE id = 1", False, "DELETE"),
        ("UPDATE table SET col = 1", False, "UPDATE"),
        ("DROP TABLE table", False, "DROP"),
        ("TRUNCATE TABLE table", False, "TRUNCATE"),
        ("ALTER TABLE table ADD COLUMN col", False, "ALTER"),
        ("SELECT * FROM table; DELETE FROM table2", False, "multiple statements"),
        ("", False, "empty"),
    ]
    
    print("SQL Safety Validator Test Results:")
    print("=" * 80)
    
    for query, expected_valid, expected_keyword in test_queries:
        is_valid, error = SQLSafetyValidator.validate(query)
        status = "✓ PASS" if is_valid == expected_valid else "✗ FAIL"
        print(f"{status} | Valid: {is_valid} | Query: {query[:50]}...")
        if error:
            print(f"       Error: {error}")
    
    print("=" * 80)
    print(f"Blocked keywords: {', '.join(SQLSafetyValidator.get_blocked_patterns())}")
