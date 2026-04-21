import os
import secrets
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

bearer_scheme = HTTPBearer()

def get_api_token() -> str:
    token = os.getenv("ADMIN_API_TOKEN", "")
    if not token:
        raise RuntimeError("ADMIN_API_TOKEN is not set in .env")
    return token

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> str:
    expected = get_api_token()
    if not secrets.compare_digest(credentials.credentials, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials
