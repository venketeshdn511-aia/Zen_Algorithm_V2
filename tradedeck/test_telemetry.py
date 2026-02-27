import urllib.request

try:
    req = urllib.request.Request('http://127.0.0.1:8000/api/v1/observe/telemetry')
    res = urllib.request.urlopen(req)
    print("200 OK")
    print(res.read().decode())
except urllib.error.HTTPError as e:
    print(f"HTTP Error {e.code}")
    print(e.read().decode())
except Exception as e:
    print(f"Other Error: {e}")
