import os
import sys
import logging
from dotenv import load_dotenv
from fyers_apiv3 import fyersModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def exchange_code(auth_code):
    load_dotenv()
    
    app_id = os.getenv("FYERS_APP_ID")
    secret_id = os.getenv("FYERS_SECRET_ID")
    redirect_uri = os.getenv("FYERS_REDIRECT_URI")

    if not all([app_id, secret_id, redirect_uri]):
        logger.error("Missing FYERS credentials in .env")
        return

    session = fyersModel.SessionModel(
        client_id=app_id,
        secret_key=secret_id,
        redirect_uri=redirect_uri,
        response_type="code",
        grant_type="authorization_code"
    )

    # If the user passed the full string from URL
    if "auth_code=" in auth_code:
        from urllib.parse import urlparse, parse_qs
        if "?" in auth_code:
            parsed = urlparse(auth_code)
            auth_code = parse_qs(parsed.query).get("auth_code", [auth_code])[0]
        else:
            # Handle cases like auth_code=XYZ&state=auth
            parts = parse_qs(auth_code)
            auth_code = parts.get("auth_code", [auth_code])[0]
    
    logger.info(f"Using Auth Code (first 10 chars): {auth_code[:10]}...")

    session.set_token(auth_code)
    response = session.generate_token()

    if response.get("s") != "ok":
        logger.error(f"Fyers API Error: {response}")
        return

    access_token = response.get("access_token")
    refresh_token = response.get("refresh_token")

    logger.info("Successfully generated tokens")

    # Update .env
    with open(".env", "r") as f:
        lines = f.readlines()

    with open(".env", "w") as f:
        for line in lines:
            if line.startswith("FYERS_ACCESS_TOKEN="):
                f.write(f"FYERS_ACCESS_TOKEN={access_token}\n")
            elif line.startswith("FYERS_REFRESH_TOKEN="):
                f.write(f"FYERS_REFRESH_TOKEN={refresh_token}\n")
            else:
                f.write(line)

    logger.info("Updated .env file with new tokens")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python exchange_code_for_tokens.py <auth_code>")
        sys.exit(1)
    
    exchange_code(sys.argv[1])
