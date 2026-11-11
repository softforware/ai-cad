#!/bin/sh
# Start the AI CAD app at http://127.0.0.1:8765 (override with PORT=...)
# AI chat reads ANTHROPIC_API_KEY from .env; the parameter panel works without it.
cd "$(dirname "$0")"
exec .venv/bin/uvicorn server:app --host 127.0.0.1 --port "${PORT:-8765}" "$@"
