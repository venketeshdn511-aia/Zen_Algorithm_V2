import asyncio
import hashlib
import os
import httpx
from dotenv import load_dotenv

load_dotenv('.env')

APP_ID = os.environ.get('FYERS_APP_ID')
SECRET_ID = os.environ.get('FYERS_SECRET_ID')
MONGO_URI = os.environ.get('MONGODB_URI')

if not MONGO_URI:
    MONGO_URI = 'mongodb+srv://venketeshdn511:Venkatesh%401990@cluster0.dbwvn.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0'

auth_code = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhcHBfaWQiOiJWV0tZMEE2TUFMIiwidXVpZCI6ImI4ZTM2ODcxZjc2ZTRkYWI4MGNlY2YyZDRmYWEzYTI5IiwiaXBBZGRyIjoiIiwibm9uY2UiOiIiLCJzY29wZSI6IiIsImRpc3BsYXlfbmFtZSI6IlhWMzAyODYiLCJvbXMiOiJLMSIsImhzbV9rZXkiOiJiNDU2NDY1NmQ2OGY1ZmE0ODlhZGU1ZjgzZGM4ZDc4OGMxZGFjMWEzMjIxYjk2YzhmNjBmYmE0MiIsImlzRGRwaUVuYWJsZWQiOiJOIiwiaXNNdGZFbmFibGVkIjoiTiIsImF1ZCI6IltcImQ6MVwiLFwiZDoyXCIsXCJ4OjBcIixcIng6MVwiLFwieDoyXCJdIiwiZXhwIjoxNzc2Mjg2NDUxLCJpYXQiOjE3NzYyNTY0NTEsImlzcyI6ImFwaS5sb2dpbi5meWVycy5pbiIsIm5iZiI6MTc3NjI1NjQ1MSwic3ViIjoiYXV0aF9jb2RlIn0.lGukOrAHgbIU50QR8xtGjp2wTPPKFkpDU4sMvwChMvc"

async def main():
    if not APP_ID or not SECRET_ID:
        print("Missing APP_ID or SECRET_ID")
        return
        
    app_hash = hashlib.sha256(f"{APP_ID}:{SECRET_ID}".encode()).hexdigest()
    async with httpx.AsyncClient() as client:
        r5 = await client.post('https://api-t1.fyers.in/api/v3/validate-authcode', json={
            'grant_type': 'authorization_code', 'appIdHash': app_hash, 'code': auth_code
        })
        data = r5.json()
        print('FYERS RESPONSE:', data)
        if r5.status_code != 200 or data.get('s') == 'error': 
            print('Failed to get token!')
            return
            
        new_token = data.get('access_token')
        print('Generated Token:', new_token[:20] + '...')
        
        from app.services.mongodb_service import MongoDBService
        
        mongo = MongoDBService(MONGO_URI, 'tradedeck')
        await mongo.connect()
        await mongo.set_config('fyers_access_token', new_token)
        print('Successfully uploaded to MongoDB!')
        await mongo.close()

if __name__ == '__main__':
    asyncio.run(main())
