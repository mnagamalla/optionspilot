from dotenv import load_dotenv
load_dotenv()

import warnings
warnings.filterwarnings("ignore", message=".*OpenSSL.*")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response

from backend.routers import csp, covered_calls, journal, unusual_flow, ask
from backend.db.database import init_db

app = FastAPI(
    title="OptionsPilot API",
    description="Wheel strategy scanner + options journal",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # Tighten this when hosted
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup():
    init_db()

app.include_router(csp.router)
app.include_router(covered_calls.router)
app.include_router(journal.router)
app.include_router(unusual_flow.router)
app.include_router(ask.router)

app.mount("/static", StaticFiles(directory="frontend"), name="static")

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)

@app.get("/", include_in_schema=False)
def serve_ui():
    return FileResponse("frontend/index.html")

@app.get("/health", tags=["Health"])
def health():
    return {"status": "ok", "version": "1.0.0"}
