import os
import hashlib
import httpx
from dotenv import load_dotenv

def test_refresh():
    load_dotenv()
    app_id = os.getenv("FYERS_APP_ID")
    secret_id = os.getenv("FYERS_SECRET_ID")
    refresh_token = os.getenv("FYERS_REFRESH_TOKEN")
    pin = os.getenv("FYERS_PIN")

    if not all([app_id, secret_id, refresh_token, pin]):
        print("Missing credentials in .env")
        return

    # appIdHash = sha256(app_id + ":" + secret_id)
    hash_input = f"{app_id}:{secret_id}"
    app_id_hash = hashlib.sha256(hash_input.encode()).hexdigest()

    payload = {
        "grant_type": "refresh_token",
        "appIdHash": app_id_hash,
        "refresh_token": refresh_token,
        "pin": pin
    }

    url = "https://api-t1.fyers.in/api/v3/validate-refresh-token"
    
    print(f"Testing refresh with AppID: {app_id}")
    
    with httpx.Client() as client:
        res = client.post(url, json=payload)
        print(f"Status Code: {res.status_code}")
        data = res.json()
        print(f"Response: {data}")
        
        if data.get("s") == "ok":
            print("Refresh Successful!")
            print(f"New Access Token: {data.get('access_token')[:20]}...")
        else:
            print("Refresh Failed.")

if __name__ == "__main__":
    test_refresh()
