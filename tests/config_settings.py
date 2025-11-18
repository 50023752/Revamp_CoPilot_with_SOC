import os
import sys

print("--- Starting Configuration Test ---")

try:
    # Add the current directory to the Python path
    # This helps find the 'config' module if you run this from the root
    sys.path.append(os.getcwd())
    
    from config import settings
    print("Successfully imported 'config.settings'")
    
    # 1. Test for GOOGLE_API_KEY
    api_key = settings.google_api_key
    if api_key:
        # Only show the first 4 and last 4 characters for security
        print(f"GOOGLE_API_KEY:    Loaded (sk_...{api_key[-4:]})")
    else:
        print("GOOGLE_API_KEY:    *** NOT FOUND / EMPTY ***")

    # 2. Test for GEMINI_FLASH_MODEL (The one causing the 404 error)
    flash_model = settings.gemini_flash_model
    if flash_model:
        print(f"GEMINI_FLASH_MODEL:  '{flash_model}'")
    else:
        print("GEMINI_FLASH_MODEL:  *** NOT FOUND / EMPTY ***")

    print("\n--- Test Complete ---")

    if not api_key or not flash_model:
        print("\n[!] WARNING: One or more critical settings are missing.")
        print("    Ensure your .env file is in the correct location and has these values.")
        sys.exit(1) # Exit with an error code

except ImportError:
    print("\n[X] CRITICAL ERROR: Could not import 'config.settings'.")
    print("    Make sure you are running this script from your project's root directory")
    print("    and that 'config/__init__.py' and 'config/settings.py' exist.")
    sys.exit(1)

except Exception as e:
    print(f"\n[X] An unexpected error occurred: {e}")
    sys.exit(1)