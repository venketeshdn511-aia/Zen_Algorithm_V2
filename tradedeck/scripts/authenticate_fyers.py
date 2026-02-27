import os
import pyotp
import httpx
import logging
from dotenv import load_dotenv
from fyers_apiv3 import fyersModel

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def authenticate():
    load_dotenv()
    
    app_id = os.getenv("FYERS_APP_ID")
    secret_id = os.getenv("FYERS_SECRET_ID")
    redirect_uri = os.getenv("FYERS_REDIRECT_URI")
    username = os.getenv("FYERS_USERNAME")
    pin = os.getenv("FYERS_PIN")
    totp_secret = os.getenv("FYERS_TOTP_SECRET")

    if not all([app_id, secret_id, redirect_uri, username, pin, totp_secret]):
        logger.error("Missing credentials in .env file")
        return

    # 1. Generate TOTP
    totp = pyotp.TOTP(totp_secret).now()
    logger.info(f"Generated TOTP: {totp}")

    # 2. Automated Login to get Auth Code
    # This involves calling Fyers authentication endpoints
    # First, send username to fyers to get session
    client = httpx.Client()
    
    # Step 1: Send Username
    # Use api.fyers.in v3 endpoints
    base_login_url = "https://api.fyers.in/google-auth/login/v3"
    payload_step1 = {"fy_id": username, "app_id": "2"}
    res1 = client.post(f"{base_login_url}/send-login-otp", json=payload_step1)
    if res1.status_code != 200:
        logger.error(f"Failed Step 1: {res1.text}")
        return
    
    request_key = res1.json().get("request_key")
    logger.info(f"Got Request Key: {request_key}")

    # Step 2: Verify PIN and TOTP
    payload_step2 = {
        "request_key": request_key,
        "identity_type": "pin",
        "identifier": pin,
        "otp": totp
    }
    res2 = client.post(f"{base_login_url}/verify-login-otp", json=payload_step2)
    if res2.status_code != 200:
        logger.error(f"Failed Step 2: {res2.text}")
        return
    
    access_token_v2 = res2.json().get("data", {}).get("access_token")
    if not access_token_v2:
        logger.error("Failed to get access token from Step 2")
        return
    
    logger.info("Successfully logged into Fyers session")

    # Step 3: Use the session to get Auth Code
    session = fyersModel.SessionModel(
        client_id=app_id,
        secret_key=secret_id,
        redirect_uri=redirect_uri,
        response_type="code",
        grant_type="authorization_code"
    )
    
    auth_url = session.generate_authcode()
    
    from urllib.parse import urlparse, parse_qs
    parsed_url = urlparse(auth_url)
    params = parse_qs(parsed_url.query)
    
    payload_auth = {
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": params.get("state", [""])[0],
        "scope": "",
        "nonce": "",
        "app_id": app_id
    }
    
    # Update headers with session token
    client.headers.update({"Authorization": f"Bearer {access_token_v2}"})
    
    # Hit the v3 auth-code endpoint
    res3 = client.get(f"https://api-t1.fyers.in/api/v3/auth-code", params=payload_auth, follow_redirects=False)
    
    if "Location" not in res3.headers:
        logger.error(f"Authorization failed, no redirect: {res3.text}")
        return
    
    redirect_location = res3.headers["Location"]
    logger.info(f"Redirect Location: {redirect_location}")
    
    # Extract auth_code from redirect URL
    parsed_redirect = urlparse(redirect_location)
    auth_code = parse_qs(parsed_redirect.query).get("auth_code", [""])[0]
    
    if not auth_code:
        logger.error("Failed to extract auth_code from redirect")
        return
    
    logger.info(f"Extracted Auth Code: {auth_code}")

    # Step 4: Exchange Auth Code for Access Token
    session.set_token(auth_code)
    response = session.generate_access_token()
    
    if response.get("s") != "ok":
        logger.error(f"Token generation failed: {response}")
        return
    
    new_access_token = response.get("access_token")
    new_refresh_token = response.get("refresh_token")
    
    logger.info("Successfully generated new tokens!")

    # Step 5: Update .env file
    with open(".env", "r") as f:
        lines = f.readlines()
    
    with open(".env", "w") as f:
        for line in lines:
            if line.startswith("FYERS_ACCESS_TOKEN="):
                f.write(f"FYERS_ACCESS_TOKEN={new_access_token}\n")
            elif line.startswith("FYERS_REFRESH_TOKEN="):
                f.write(f"FYERS_REFRESH_TOKEN={new_refresh_token}\n")
            else:
                f.write(line)
    
    logger.info("Updated .env file successfully")

if __name__ == "__main__":
    authenticate()
