import requests
import json

BASE_URL = "http://127.0.0.1:8000/api/v1/observe"
HEADERS = {"X-Auth-Token": "local-dev"}

def test_endpoint(path):
    print(f"\n--- Testing {path} ---")
    try:
        resp = requests.get(f"{BASE_URL}{path}", headers=HEADERS)
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print(json.dumps(data, indent=2))
        else:
            print(resp.text)
    except Exception as e:
        print(f"Error: {e}")

test_endpoint("/infra")
test_endpoint("/telemetry")
