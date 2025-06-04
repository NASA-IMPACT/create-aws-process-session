#!/usr/bin/env python3

import os
import sys
import stat
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
import json
import requests
from datetime import datetime, timezone, timedelta

CACHE_FILE = os.path.expanduser("~/.aws/credentials_cache_python.json")
API_URL = "API_URL_PLACEHOLDER"
API_KEY = "API_KEY_PLACEHOLDER"
EXPIRATION_THRESHOLD = timedelta(minutes=5)  # Refresh if expiring within 5 min

def get_cached_credentials():
    """Reads cached credentials if they exist and are valid."""
    if not os.path.exists(CACHE_FILE):
        return None

    with open(CACHE_FILE, "r") as f:
        data = json.load(f)

    expiration_time = datetime.fromisoformat(data.get("Expiration")).replace(tzinfo=timezone.utc)
    current_time = datetime.now(timezone.utc)  # Ensure UTC comparison

    if current_time < (expiration_time - EXPIRATION_THRESHOLD):
        return data  # Return valid cached credentials

    return None  # Expired credentials

def fetch_new_credentials():
    """Fetches new credentials from the API and saves them to cache."""
    try:
        response = requests.get(API_URL, headers={"api-key": API_KEY}, timeout=5)
        response.raise_for_status()
        credentials = response.json()

        # Ensure Expiration is stored as a proper UTC timestamp
        credentials["Expiration"] = datetime.fromisoformat(credentials["Expiration"]).replace(tzinfo=timezone.utc).isoformat()

        with open(CACHE_FILE, "w") as f:
            json.dump(credentials, f)

        return credentials
    except requests.RequestException as e:
        print(json.dumps({"error": "Failed to fetch credentials"}))
        exit(1)

if __name__ == "__main__":
    credentials = get_cached_credentials() or fetch_new_credentials()
    print(json.dumps(credentials))  # AWS CLI reads this output
'''
    
    # Replace placeholders with actual values
    script_content = script_template.replace("API_URL_PLACEHOLDER", api_url).replace("API_KEY_PLACEHOLDER", api_key)
    
    # Write the script
    with open(script_path, 'w') as f:
        f.write(script_content)
    
    # Make the script executable
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)
    
    print(f"✓ Created executable script: {script_path}")
    return script_path

def update_aws_credentials_config(script_path):
    """Updates ~/.aws/credentials to include the temp-creds-session profile"""
    
    credentials_file = Path.home() / '.aws' / 'credentials'
    
    # Read existing credentials file or create new config
    config = configparser.ConfigParser()
    if credentials_file.exists():
        config.read(credentials_file)
    
    # Add or update the temp-creds-session profile
    if 'temp-creds-session' not in config:
        config.add_section('temp-creds-session')
    
    config.set('temp-creds-session', 'credential_process', str(script_path))
    
    # Write back to file
    with open(credentials_file, 'w') as f:
        config.write(f)
    
    print(f"✓ Updated AWS credentials file: {credentials_file}")
    print("✓ Added [temp-creds-session] profile with credential_process")

def main():
    print("AWS Temporary Credentials Setup")
    print("=" * 35)
    
    try:
        # Create the credentials script
        script_path = create_temp_creds_script()
        
        # Update AWS credentials configuration
        update_aws_credentials_config(script_path)
        
        print("\n🎉 Setup completed successfully!")
        print("\nYou can now use AWS CLI with:")
        print("  aws --profile temp-creds-session <command>")
        print("\nOr set as default profile:")
        print("  export AWS_PROFILE=temp-creds-session")
        
    except Exception as e:
        print(f"❌ Setup failed: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
