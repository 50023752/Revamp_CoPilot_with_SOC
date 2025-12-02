#!/usr/bin/env python3
"""
Multi-User Credential Generator for Orion Copilot
Generates password hashes for multiple team users
"""

import sys
import os

# Add parent directory to path to import auth_utils
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.auth_utils import hash_password


def generate_multi_user_credentials():
    """Interactive tool to generate credentials for multiple teams"""
    
    print("=" * 70)
    print("  Orion Copilot - Multi-User Credential Generator")
    print("=" * 70)
    print()
    print("This tool will help you generate password hashes for your team users.")
    print()
    
    # Define team users
    teams = [
        {"username": "risk_team_user", "team": "Risk Team"},
        {"username": "credit_team_user", "team": "Credit Team"},
        {"username": "collection_team_user", "team": "Collection Team"}
    ]
    
    credentials = []
    
    for team in teams:
        print(f"🔐 {team['team']} ({team['username']})")
        print("-" * 70)
        
        password = input(f"Enter password for {team['username']}: ")
        
        if not password:
            print(f"⚠️  Skipping {team['username']} (no password provided)")
            print()
            continue
        
        # Generate hash
        password_hash = hash_password(password)
        
        credentials.append({
            "username": team["username"],
            "password_hash": password_hash
        })
        
        print(f"✅ Hash generated: {password_hash[:32]}...")
        print()
    
    if not credentials:
        print("❌ No credentials generated. Exiting.")
        return
    
    # Generate JSON for deployment scripts
    print("=" * 70)
    print("  Generated Credentials (JSON Format)")
    print("=" * 70)
    print()
    
    print("📋 Copy the following to your deployment script:")
    print()
    print("USER_CREDENTIALS='[")
    for i, cred in enumerate(credentials):
        comma = "," if i < len(credentials) - 1 else ""
        print(f'  {{"username": "{cred["username"]}", "password_hash": "{cred["password_hash"]}"}}{comma}')
    print("]'")
    print()
    
    # Generate for Linux/Mac (deploy.sh)
    print("=" * 70)
    print("  For deploy.sh (Linux/Mac)")
    print("=" * 70)
    print()
    print("Replace the USER_CREDENTIALS line with:")
    print()
    print("USER_CREDENTIALS='[\\")
    for i, cred in enumerate(credentials):
        comma = ",\\" if i < len(credentials) - 1 else "\\"
        print(f'  {{"username": "{cred["username"]}", "password_hash": "{cred["password_hash"]}"}}{comma}')
    print("]'")
    print()
    
    # Generate for Windows (deploy.bat)
    print("=" * 70)
    print("  For deploy.bat (Windows)")
    print("=" * 70)
    print()
    print("Replace the USER_CREDENTIALS line with:")
    print()
    user_creds_json = "["
    for i, cred in enumerate(credentials):
        comma = ", " if i < len(credentials) - 1 else ""
        user_creds_json += f'{{"username": "{cred["username"]}", "password_hash": "{cred["password_hash"]}"}}{comma}'
    user_creds_json += "]"
    print(f"set USER_CREDENTIALS={user_creds_json}")
    print()
    
    # Summary table
    print("=" * 70)
    print("  Login Credentials Summary")
    print("=" * 70)
    print()
    print("Share these credentials with your team members:")
    print()
    print(f"{'Username':<25} {'Team':<20} {'Password':<20}")
    print("-" * 70)
    
    for team in teams:
        matching_cred = next((c for c in credentials if c["username"] == team["username"]), None)
        if matching_cred:
            # Note: We don't store the original password, so we can't display it
            print(f"{team['username']:<25} {team['team']:<20} {'(as entered)':<20}")
    
    print()
    print("⚠️  SECURITY REMINDERS:")
    print("  - Share passwords securely (encrypted chat, password manager)")
    print("  - Never commit these hashes to Git")
    print("  - Rotate passwords every 90 days")
    print("  - Each team member should use their own credentials")
    print()


def generate_single_user():
    """Generate hash for a single user (backward compatibility)"""
    if len(sys.argv) > 2:
        username = sys.argv[1]
        password = sys.argv[2]
    else:
        print("Usage: python generate_multi_user_creds.py <username> <password>")
        print("   or: python generate_multi_user_creds.py (interactive mode)")
        return
    
    password_hash = hash_password(password)
    
    print(f"Username: {username}")
    print(f"Password Hash: {password_hash}")
    print()
    print(f'{{"username": "{username}", "password_hash": "{password_hash}"}}')


if __name__ == "__main__":
    if len(sys.argv) == 1:
        # Interactive mode
        generate_multi_user_credentials()
    else:
        # Single user mode
        generate_single_user()
