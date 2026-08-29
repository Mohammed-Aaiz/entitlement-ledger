"""Vercel serverless entry point for EntitlementLedger FastAPI.

Wraps the FastAPI ASGI app with Mangum to bridge AWS Lambda (Vercel)
to ASGI. All /api/* routes are preserved. Startup/lifespan behavior
is preserved. Local uvicorn development is unaffected.
"""
import sys
from pathlib import Path

# Ensure the backend directory is on sys.path so that imports like
# `from main import app`, `from database import ...`, etc. resolve correctly
# when Vercel runs this from api/index.py.
_backend_dir = str(Path(__file__).resolve().parent.parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from mangum import Mangum
from main import app

# Mangum adapts Vercel's Lambda event format to ASGI
handler = Mangum(app, lifespan="auto")
