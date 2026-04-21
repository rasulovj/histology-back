from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routers import stats, users, feedback, broadcast, library, admins, payments, tests, preparations

app = FastAPI(title="Histology Bot Admin API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stats.router,     prefix="/api/stats",     tags=["stats"])
app.include_router(users.router,     prefix="/api/users",     tags=["users"])
app.include_router(feedback.router,  prefix="/api/feedback",  tags=["feedback"])
app.include_router(broadcast.router, prefix="/api/broadcast", tags=["broadcast"])
app.include_router(library.router,   prefix="/api/library",   tags=["library"])
app.include_router(preparations.router, prefix="/api/preparations", tags=["preparations"])
app.include_router(admins.router,    prefix="/api/admins",    tags=["admins"])
app.include_router(payments.router,  prefix="/api/payments",  tags=["payments"])
app.include_router(tests.router,     prefix="/api/tests",     tags=["tests"])

@app.post("/api/auth/login", tags=["auth"])
async def login(body: dict):
    import os
    import secrets
    from fastapi import HTTPException

    expected_login = os.getenv("ADMIN_LOGIN", "")
    expected_password = os.getenv("ADMIN_PASSWORD", "")
    api_token = os.getenv("ADMIN_API_TOKEN", "")

    if not expected_login or not expected_password or not api_token:
        raise HTTPException(status_code=500, detail="ADMIN_LOGIN, ADMIN_PASSWORD, or ADMIN_API_TOKEN not configured")

    login_ok = secrets.compare_digest(body.get("login", ""), expected_login)
    password_ok = secrets.compare_digest(body.get("password", ""), expected_password)

    if not (login_ok and password_ok):
        raise HTTPException(status_code=401, detail="Invalid login or password")

    return {"access_token": api_token}

@app.get("/api/health", tags=["health"])
async def health():
    return {"status": "ok"}
