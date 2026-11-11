"""FastAPI app: serves the UI, the AI chat endpoint, and a direct parameter endpoint."""

import os
from pathlib import Path

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agent
import cad

app = FastAPI(title="AI CAD")
sessions: dict[str, agent.Session] = {}
ROOT = Path(__file__).parent


def load_env_file():
    """Pick up ANTHROPIC_API_KEY from ai-cad/.env, so adding a key needs no restart."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        key, sep, value = line.strip().removeprefix("export ").partition("=")
        if sep and key.strip() and not key.startswith("#"):
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


class ChatIn(BaseModel):
    session_id: str
    message: str


class GearIn(BaseModel):
    name: str = "Spur gear"
    module: float
    teeth: int
    pressure_angle: float = 20
    face_width: float = 10
    bore_diameter: float = 0
    keyway_width: float = 0
    keyway_depth: float = 0
    hub_diameter: float = 0
    hub_length: float = 0


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/status")
def status():
    load_env_file()
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    return {"ai_enabled": has_key, "model": agent.MODEL}


@app.post("/api/chat")
def chat(body: ChatIn):
    if not status()["ai_enabled"]:
        raise HTTPException(401, "AI chat needs an API key. Put the line "
                                 "ANTHROPIC_API_KEY=sk-ant-... in ~/ai-cad/.env and send again.")
    session = sessions.get(body.session_id)
    try:
        if session is None:
            session = sessions[body.session_id] = agent.Session()
        return session.chat(body.message)
    except anthropic.AuthenticationError:
        raise HTTPException(401, "Invalid or missing ANTHROPIC_API_KEY.")
    except anthropic.RateLimitError:
        raise HTTPException(429, "Rate limited by the Claude API; try again shortly.")
    except anthropic.APIConnectionError:
        raise HTTPException(502, "Could not reach the Claude API.")
    except anthropic.APIStatusError as e:
        raise HTTPException(502, f"Claude API error: {e.message}")
    except anthropic.AnthropicError as e:  # e.g. no credentials configured
        raise HTTPException(401, str(e))


@app.post("/api/gear")
def gear(body: GearIn):
    try:
        return agent.build_gear(body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))


app.mount("/output", StaticFiles(directory=cad.OUTPUT_DIR), name="output")
