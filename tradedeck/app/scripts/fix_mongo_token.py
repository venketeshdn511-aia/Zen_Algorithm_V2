import asyncio
import os
import sys
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timezone

# Add the project root to sys.path if needed
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

async def fix_token():
    # Use environment variables if available (inside docker)
    mongo_uri = os.environ.get("MONGODB_URI")
    if not mongo_uri:
        # Fallback to the one we know
        mongo_uri = "mongodb+srv://venketeshdn511:Venkatesh%401990@cluster0.dbwvn.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
    
    token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhdWQiOlsiZDoxIiwiZDoyIiwieDowIiwieDoxIiwieDoyIl0sImF0X2hhc2giOiJnQUFBQUFCcDM0WHRDNzZuNl8zdE5xNmZmSkNvNVVBRXFEUnZyTzBtZEQtV05oelhTQWJ3WkEydVp6ak5ndzJfcW5lMDYyaU5qQ3dGbVp0djE1ZmRaeUpqc3J1MGFuSVZuREgzSG5LOUV4YnB5eHFpVTc3ZmVzVT0iLCJkaXNwbGF5X25hbWUiOiIiLCJvbXMiOiJLMSIsImhzbV9rZXkiOiJiNDU2NDY1NmQ2OGY1ZmE0ODlhZGU1ZjgzZGM4ZDc4OGMxZGFjMWEzMjIxYjk2YzhmNjBmYmE0MiIsImlzRGRwaUVuYWJsZWQiOiJOIiwiaXNNdGZFbmFibGVkIjoiTiIsImZ5X2lkIjoiWFYzMDI4NiIsImFwcFR5cGUiOjIwMCwiZXhwIjoxNzc2Mjk5NDAwLCJpYXQiOjE3NzYyNTY0OTMsImlzcyI6ImFwaS5meWVycy5pbiIsIm5iZiI6MTc3NjI1NjQ5Mywic3ViIjoiYWNjZXNzX3Rva2VuIn0.TvX0xrElKqpqoKWzmiAkFi-rlfATLHMhRsdfjUaRw0I"
    
    print(f"Connecting to MongoDB...")
    client = AsyncIOMotorClient(mongo_uri)
    db = client["tradedeck"]
    
    print(f"Updating fyers_access_token in 'config' collection...")
    await db.config.update_one(
        {"key": "fyers_access_token"},
        {"$set": {"value": token, "updated_at": datetime.now(timezone.utc)}},
        upsert=True
    )
    
    print("Verification:")
    doc = await db.config.find_one({"key": "fyers_access_token"})
    if doc and doc["value"] == token:
        print("✅ SUCCESS: Token updated and verified in MongoDB.")
    else:
        print("❌ FAILURE: Token mismatch or not found.")
        
    client.close()

if __name__ == "__main__":
    asyncio.run(fix_token())
