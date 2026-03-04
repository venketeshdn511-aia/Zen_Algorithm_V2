"""
scripts/authenticate_fyers.py

Generates a fresh Fyers access_token + refresh_token using the correct
Fyers API v3 vagator/v2 endpoints (TOTP-based, fully automated).

Correct flow (per Fyers v3 reference implementation):
  1. send_login_otp_v2  → fy_id base64-encoded, returns request_key
  2. verify_otp         → verify TOTP, returns new request_key
  3. verify_pin_v2      → PIN base64-encoded, returns session access_token
  4. POST /api/v3/token → get 308 redirect with auth_code in URL
  5. POST /api/v3/validate-authcode → final access_token + refresh_token
  6. Update .env file

Usage:
  cd "C:\\Users\\Vinay\\OneDrive\\Desktop\\Algo Trading\\tradedeck-v2-production\\tradedeck"
  python scripts/authenticate_fyers.py
"""

import os
import sys
import base64
import hashlib
import pyotp
import httpx
import logging
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ── Fyers API URLs ────────────────────────────────────────────────────────────
API_T1 = "https://api-t1.fyers.in/api/v3"
API_T2 = "https://api-t2.fyers.in/vagator/v2"

HEADERS = {
    "Accept": "application/json",
    "Content-Type": "text/plain",
}


def b64(value: str) -> str:
    """Base64-encode a string (required by Fyers vagator endpoints)."""
    return base64.b64encode(value.encode()).decode()


def authenticate():
    load_dotenv()

    fy_id       = os.getenv("FYERS_USERNAME")       # e.g. XV30286
    pin         = os.getenv("FYERS_PIN")             # e.g. 8310
    totp_secret = os.getenv("FYERS_TOTP_SECRET")
    app_id      = os.getenv("FYERS_APP_ID", "")      # e.g. QTJY824GNX-100
    secret_id   = os.getenv("FYERS_SECRET_ID")
    redirect_uri = os.getenv("FYERS_REDIRECT_URI", "http://127.0.0.1:8080")

    # Split app_id into base + type (QTJY824GNX-100 → QTJY824GNX, 100)
    if "-" in app_id:
        app_base, app_type = app_id.rsplit("-", 1)
    else:
        app_base, app_type = app_id, "100"

    missing = [k for k, v in {
        "FYERS_USERNAME": fy_id, "FYERS_PIN": pin,
        "FYERS_TOTP_SECRET": totp_secret, "FYERS_APP_ID": app_id,
        "FYERS_SECRET_ID": secret_id
    }.items() if not v]
    if missing:
        logger.error(f"Missing env vars: {', '.join(missing)}")
        sys.exit(1)

    with httpx.Client(headers=HEADERS, timeout=15) as client:

        # ── Step 1: Send Login OTP ────────────────────────────────────────────
        logger.info("Step 1: Sending login OTP (fy_id base64-encoded)...")
        r1 = client.post(f"{API_T2}/send_login_otp_v2", json={
            "fy_id": b64(fy_id),
            "app_id": "2"
        })
        logger.info(f"  Status: {r1.status_code}  Body: {r1.text}")
        if r1.status_code != 200:
            logger.error(f"Step 1 failed: {r1.text}")
            sys.exit(1)

        request_key = r1.json().get("request_key")
        logger.info(f"  request_key = {request_key}")

        # ── Step 2: Verify TOTP ───────────────────────────────────────────────
        totp = pyotp.TOTP(totp_secret).now()
        logger.info(f"Step 2: Verifying TOTP = {totp}...")
        r2 = client.post(f"{API_T2}/verify_otp", json={
            "request_key": request_key,
            "otp": totp
        })
        logger.info(f"  Status: {r2.status_code}  Body: {r2.text}")
        if r2.status_code != 200:
            logger.error(f"Step 2 TOTP verify failed: {r2.text}")
            sys.exit(1)

        request_key2 = r2.json().get("request_key")
        logger.info(f"  request_key2 = {request_key2}")

        # ── Step 3: Verify PIN (base64-encoded) ───────────────────────────────
        logger.info("Step 3: Verifying PIN (base64-encoded)...")
        r3 = client.post(f"{API_T2}/verify_pin_v2", json={
            "request_key": request_key2,
            "identity_type": "pin",
            "identifier": b64(pin)
        })
        logger.info(f"  Status: {r3.status_code}  Body: {r3.text}")
        if r3.status_code != 200:
            logger.error(f"Step 3 PIN verify failed: {r3.text}")
            sys.exit(1)

        session_token = r3.json().get("data", {}).get("access_token")
        if not session_token:
            logger.error(f"No session access_token in Step 3 response: {r3.text}")
            sys.exit(1)
        logger.info("  Session token obtained ✅")

        # ── Step 4: Get Auth Code (POST → 308 redirect with auth_code) ────────
        logger.info("Step 4: Getting auth code via /api/v3/token...")
        client.headers.update({"Authorization": f"Bearer {session_token}"})

        token_payload = {
            "fyers_id": fy_id,
            "app_id": app_base,
            "redirect_uri": redirect_uri,
            "appType": app_type,
            "code_challenge": "",
            "state": "None",
            "scope": "",
            "nonce": "",
            "response_type": "code",
            "create_cookie": True
        }
        r4 = client.post(f"{API_T1}/token", json=token_payload, follow_redirects=False)
        logger.info(f"  Status: {r4.status_code}  Body: {r4.text[:300]}")

        # Fyers returns 308 with auth_code in the redirect URL
        auth_code = None
        if r4.status_code == 308:
            redirect_url = r4.json().get("Url", "")
            auth_code = parse_qs(urlparse(redirect_url).query).get("auth_code", [""])[0]
        elif r4.status_code == 200:
            # Some versions return 200 with the URL in body
            redirect_url = r4.json().get("Url", "")
            if redirect_url:
                auth_code = parse_qs(urlparse(redirect_url).query).get("auth_code", [""])[0]

        if not auth_code:
            logger.error(f"Could not extract auth_code. Full response: {r4.text}")
            sys.exit(1)

        logger.info(f"  auth_code = {auth_code[:20]}... ✅")

        # ── Step 5: Exchange Auth Code for Access + Refresh Token ─────────────
        logger.info("Step 5: Generating access_token + refresh_token...")
        app_id_hash = hashlib.sha256(f"{app_base}-{app_type}:{secret_id}".encode()).hexdigest()

        r5 = client.post(f"{API_T1}/validate-authcode", json={
            "grant_type": "authorization_code",
            "appIdHash": app_id_hash,
            "code": auth_code
        })
        logger.info(f"  Status: {r5.status_code}  Body: {r5.text[:300]}")

        if r5.status_code != 200 or r5.json().get("s") == "error":
            logger.error(f"Step 5 failed: {r5.text}")
            sys.exit(1)

        data = r5.json()
        new_access_token  = data.get("access_token")
        new_refresh_token = data.get("refresh_token")

        if not new_access_token:
            logger.error(f"No access_token in response: {data}")
            sys.exit(1)

    # ── Print tokens ──────────────────────────────────────────────────────────
    logger.info("✅ Tokens generated successfully!")
    print(f"\n{'='*60}")
    print(f"FYERS_ACCESS_TOKEN={new_access_token}")
    if new_refresh_token:
        print(f"FYERS_REFRESH_TOKEN={new_refresh_token}")
    print(f"{'='*60}\n")

    # ── Update .env file ──────────────────────────────────────────────────────
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path   = os.path.join(script_dir, ".env")

    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            lines = f.readlines()

        updated = []
        found_access = found_refresh = False
        for line in lines:
            if line.startswith("FYERS_ACCESS_TOKEN="):
                updated.append(f"FYERS_ACCESS_TOKEN={new_access_token}\n")
                found_access = True
            elif line.startswith("FYERS_REFRESH_TOKEN=") and new_refresh_token:
                updated.append(f"FYERS_REFRESH_TOKEN={new_refresh_token}\n")
                found_refresh = True
            else:
                updated.append(line)

        if not found_access:
            updated.append(f"FYERS_ACCESS_TOKEN={new_access_token}\n")
        if not found_refresh and new_refresh_token:
            updated.append(f"FYERS_REFRESH_TOKEN={new_refresh_token}\n")

        with open(env_path, "w") as f:
            f.writelines(updated)

        logger.info(f"✅ Updated .env at {env_path}")
    else:
        logger.warning(f".env not found at {env_path}. Copy tokens above manually.")

    print("📋 Next: Copy the tokens above into Render → Environment Variables, then redeploy.\n")


if __name__ == "__main__":
    authenticate()
