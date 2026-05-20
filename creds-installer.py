#!/usr/bin/env python3

import os
import sys
import configparser
from pathlib import Path

def create_temp_creds_script():
    """Creates the get_temp_creds.py script in ~/.aws/"""
    
    # Check for required environment variables
    api_url = os.getenv('AWS_GET_TEMP_CREDS_API_URL')
    api_key = os.getenv('AWS_GET_TEMP_CREDS_API_KEY')
    
    if not api_url or not api_key:
        print("ERROR: Required environment variables not set!")
        print("Please set the following environment variables:")
        print("  export AWS_GET_TEMP_CREDS_API_URL='your-api-url'")
        print("  export AWS_GET_TEMP_CREDS_API_KEY='your-api-key'")
        sys.exit(1)
    
    # Create ~/.aws directory if it doesn't exist
    aws_dir = Path.home() / '.aws'
    aws_dir.mkdir(exist_ok=True)
    
    # Path for the credentials script
    script_path = aws_dir / 'get_temp_creds.py'
    
    # Get the current Python executable path
    python_path = sys.executable
    
    # Script content template
    script_template = '''#!/usr/bin/env python3

import os
import sys
import json
import requests
from datetime import datetime, timezone, timedelta

CACHE_FILE = os.path.expanduser("~/.aws/credentials_cache_python.json")
API_URL = "API_URL_PLACEHOLDER"
API_KEY = "API_KEY_PLACEHOLDER"
EXPIRATION_THRESHOLD = timedelta(minutes=5)  # Refresh if expiring within 5 min

def get_cached_credentials():
    """Reads cached credentials if they exist and are valid. Returns None on any cache problem."""
    if not os.path.exists(CACHE_FILE):
        return None

    try:
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)
        expiration = datetime.fromisoformat(data["Expiration"])
        if expiration.tzinfo is None:
            expiration = expiration.replace(tzinfo=timezone.utc)
    except (KeyError, ValueError, json.JSONDecodeError, OSError):
        return None  # corrupt or schema-incompatible cache; treat as missing

    if datetime.now(timezone.utc) < (expiration - EXPIRATION_THRESHOLD):
        return data
    return None

def fetch_new_credentials():
    """Fetches new credentials from the API. Retries once on 5xx / network blip."""
    response = None
    for attempt in range(2):
        try:
            response = requests.get(API_URL, headers={"x-api-key": API_KEY}, timeout=5)
            response.raise_for_status()
            break
        except requests.HTTPError as e:
            transient = e.response is not None and 500 <= e.response.status_code < 600
            if transient and attempt == 0:
                continue
            body = e.response.text[:300] if e.response is not None else ""
            print(f"HTTP {e.response.status_code} from credentials API: {body}", file=sys.stderr)
            sys.exit(1)
        except requests.RequestException as e:
            if attempt == 0:
                continue
            print(f"Network error contacting credentials API: {e}", file=sys.stderr)
            sys.exit(1)

    credentials = response.json()

    expiration = datetime.fromisoformat(credentials["Expiration"])
    if expiration.tzinfo is None:
        expiration = expiration.replace(tzinfo=timezone.utc)
    credentials["Expiration"] = expiration.astimezone(timezone.utc).isoformat()

    with open(CACHE_FILE, "w") as f:
        json.dump(credentials, f)
    return credentials

if __name__ == "__main__":
    credentials = get_cached_credentials() or fetch_new_credentials()
    print(json.dumps(credentials))  # AWS CLI reads this output
'''
    
    # Replace placeholders with actual values
    script_content = script_template.replace("API_URL_PLACEHOLDER", api_url).replace("API_KEY_PLACEHOLDER", api_key)
    
    # Write the script
    with open(script_path, 'w') as f:
        f.write(script_content)
    
    # Make the script executable, owner-only — the API key is baked into the file.
    script_path.chmod(0o700)
    
    print(f"✓ Created executable script: {script_path}")
    return script_path

def update_aws_credentials_config(script_path, aws_profile_name):
    """Updates ~/.aws/credentials to include a custom profile"""
    
    
    credentials_file = Path.home() / '.aws' / 'credentials'
    
    # Read existing credentials file or create new config
    config = configparser.ConfigParser()
    if credentials_file.exists():
        config.read(credentials_file)
    
    # Add or update the custom profile
    if aws_profile_name not in config:
        config.add_section(aws_profile_name)
    
    config.set(aws_profile_name, 'credential_process', str(script_path))
    
    # Write back to file
    with open(credentials_file, 'w') as f:
        config.write(f)
    
    print(f"✓ Updated AWS credentials file: {credentials_file}")
    print(f"✓ Added [{aws_profile_name}] profile with credential_process")

def main():
    aws_profile_name = os.getenv("AWS_PROFILE", "temp-creds-session")
    print("AWS Temporary Credentials Setup")
    print("=" * 35)
    
    try:
        # Create the credentials script
        script_path = create_temp_creds_script()
        
        # Update AWS credentials configuration
        update_aws_credentials_config(script_path, aws_profile_name)
        
        print("\n🎉 Setup completed successfully!")
        print("\nYou can now use AWS CLI with:")
        print(f"aws --profile {aws_profile_name} <command>")
        print("\nOr set as default profile:")
        print(f"export AWS_PROFILE={aws_profile_name}")
        
    except Exception as e:
        print(f"❌ Setup failed: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
