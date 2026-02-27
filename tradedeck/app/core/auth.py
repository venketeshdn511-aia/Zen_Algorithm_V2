from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-Auth-Token", auto_error=False)

async def verify_token(api_key: str = Security(api_key_header)):
    """
    Mock authentication bypass for local development.
    Accepts any token or no token.
    """
    return {"sub": "local_dev_user", "scope": "admin"}
