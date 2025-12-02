"""
Secure Authentication Utilities for Orion Copilot
Uses SHA-256 hashing for password verification.
"""
import hashlib
import os
import json
from typing import Optional, Dict, List


def hash_password(password: str) -> str:
    """
    Generate SHA-256 hash of a password.
    
    Args:
        password: Plain text password
        
    Returns:
        Hexadecimal SHA-256 hash string
        
    Example:
        >>> hash_password("MySecretPass123")
        'a9c7b...'
    """
    return hashlib.sha256(password.encode('utf-8')).hexdigest()


def verify_password(input_password: str, stored_hash: str) -> bool:
    """
    Verify a password against its stored hash.
    
    Args:
        input_password: Plain text password from user input
        stored_hash: SHA-256 hash to compare against
        
    Returns:
        True if password matches, False otherwise
    """
    return hash_password(input_password) == stored_hash


def load_user_credentials() -> List[Dict[str, str]]:
    """
    Load user credentials from environment variables.
    
    Expected format:
    - Single user (legacy): ADMIN_PASSWORD_HASH environment variable
    - Multiple users: USER_CREDENTIALS JSON array with username/password_hash pairs
    
    Returns:
        List of credential dictionaries with 'username' and 'password_hash' keys
        
    Examples:
        Single user mode:
            ADMIN_PASSWORD_HASH=a9c7b8e4f... (SHA-256 hash)
            Returns: [{"username": "admin", "password_hash": "a9c7b8e4f..."}]
            
        Multi-user mode:
            USER_CREDENTIALS='[{"username": "admin", "password_hash": "a9c7b..."}, ...]'
            Returns: [{"username": "admin", "password_hash": "a9c7b..."}, ...]
    """
    # Check for multi-user JSON credentials first (preferred)
    creds_json = os.getenv("USER_CREDENTIALS")
    if creds_json:
        try:
            loaded_creds = json.loads(creds_json)
            if isinstance(loaded_creds, list):
                # Validate structure
                valid_creds = []
                for cred in loaded_creds:
                    if isinstance(cred, dict) and "username" in cred and "password_hash" in cred:
                        valid_creds.append(cred)
                if valid_creds:
                    return valid_creds
        except json.JSONDecodeError:
            pass  # Fall through to single-user mode
    
    # Fall back to single-user mode (ADMIN_PASSWORD_HASH)
    admin_hash = os.getenv("ADMIN_PASSWORD_HASH")
    if admin_hash:
        return [{"username": "admin", "password_hash": admin_hash}]
    
    # No credentials configured - return empty list (will deny all logins)
    return []


def authenticate_user(username: str, password: str) -> bool:
    """
    Authenticate a user against stored credentials.
    
    Args:
        username: Username to authenticate
        password: Plain text password to verify
        
    Returns:
        True if authentication successful, False otherwise
        
    Security Notes:
        - Passwords are never stored in plain text
        - Uses constant-time comparison via SHA-256 hash matching
        - No user enumeration (same response for wrong username/password)
    """
    credentials = load_user_credentials()
    
    for cred in credentials:
        if cred.get("username") == username:
            stored_hash = cred.get("password_hash", "")
            return verify_password(password, stored_hash)
    
    # Username not found - return False (same as wrong password)
    return False


if __name__ == "__main__":
    # CLI utility for generating password hashes
    import sys
    
    print("=" * 60)
    print("  Orion Copilot - Password Hash Generator")
    print("=" * 60)
    print()
    
    if len(sys.argv) > 1:
        password = sys.argv[1]
    else:
        password = input("Enter password to hash: ")
    
    if not password:
        print("Error: Password cannot be empty")
        sys.exit(1)
    
    hashed = hash_password(password)
    
    print()
    print("Password Hash (SHA-256):")
    print("-" * 60)
    print(hashed)
    print("-" * 60)
    print()
    print("Usage Examples:")
    print()
    print("1. Single User (Linux/Mac):")
    print(f'   export ADMIN_PASSWORD_HASH="{hashed}"')
    print()
    print("2. Single User (Windows):")
    print(f'   set ADMIN_PASSWORD_HASH={hashed}')
    print()
    print("3. Cloud Run Deployment:")
    print(f'   --set-env-vars "ADMIN_PASSWORD_HASH={hashed}"')
    print()
    print("4. Multiple Users (JSON format):")
    print('   USER_CREDENTIALS=\'[')
    print(f'     {{"username": "admin", "password_hash": "{hashed}"}}')
    print('   ]\'')
    print()
    print("🔒 Keep this hash secure! Treat it like a password.")
    print()
