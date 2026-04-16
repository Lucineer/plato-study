"""Minimal PLATO Study HTTP server for Codespaces."""
import yaml, os, sys, importlib.util, json
from pathlib import Path
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any
import uvicorn

app = FastAPI(title="PLATO Study")
BASE = Path(__file__).parent.parent / "world"

# Agent gateway
sys.path.insert(0, str(Path(__file__).parent.parent / "bridges"))
import agent_gateway
_gateway = agent_gateway

_sessions = {}  # session_id -> username mapping

def read_yaml(p):
    try: return yaml.safe_load(open(p)) or {}
    except: return {}

def write_yaml(p, d):
    t = str(p)+".tmp"
    open(t,"w").write(yaml.dump(d, default_flow_style=False))
    os.replace(t, p)

# Seed agent tasks on import
_gateway.AGENT_DIR.mkdir(parents=True, exist_ok=True)
_gateway.seed_tasks()

class Command(BaseModel):
    agent: str = "unknown"
    action: str = "status"
    expert_id: Optional[str] = None
    expert: Optional[str] = None
    topic: Optional[str] = None
    brief: Optional[str] = None
    model: Optional[str] = None
    budget_tokens: Optional[int] = None
    max_rounds: Optional[int] = None
    name: Optional[str] = None
    content: Optional[str] = None
    entry_type: Optional[str] = None
    type: Optional[str] = None
    sha: Optional[str] = None
    checkpoint_label: Optional[str] = None
    new_expert_name: Optional[str] = None
    label: Optional[str] = None
    note: Optional[str] = None

    class Config:
        extra = "allow"

@app.post("/command")
async def run_command(cmd: Command):
    try:
        cid = f"{os.urandom(4).hex()}"
        cmds = BASE / "commands"
        cmds.mkdir(parents=True, exist_ok=True)
        write_yaml(cmds / f"{cid}.yaml", cmd.dict(exclude_none=True))
        sys.path.insert(0, str(Path(__file__).parent.parent / "bridges"))
        import study_engine
        study_engine.process_turns()
        logs = sorted((BASE / "logs").glob("*.yaml"))
        return read_yaml(logs[-1]) if logs else {"status": "processed"}
    except Exception as e:
        import traceback
        return JSONResponse(status_code=200, content={"error": str(e), "traceback": traceback.format_exc()})

@app.get("/status")
async def status():
    rooms = {}
    for f in (BASE / "rooms").glob("*.yaml"):
        rooms[f.stem] = read_yaml(f)
    return rooms

@app.get("/experts")
async def experts():
    return {"experts": [read_yaml(f) for f in (BASE/"experts").glob("*.yaml") if read_yaml(f).get("id")]}

@app.get("/journal")
async def journal(limit: int = 50):
    return {"entries": [read_yaml(f) for f in sorted((BASE/"journals").glob("*.yaml"))[-limit:]]}

@app.post("/agent/login")
async def agent_login(body: dict):
    """Agent authenticates with username/password, gets session token."""
    username = body.get("username", "")
    password = body.get("password", "")
    session, err = _gateway.authenticate(username, password)
    if err:
        return JSONResponse(status_code=401, content={"error": err})
    _sessions[session["session_id"]] = username
    onboarding = _gateway.get_onboarding(username)
    return {"session_id": session["session_id"], "username": username, "onboarding": onboarding}

@app.post("/agent/command")
async def agent_command(body: dict):
    """Agent sends a text command. Session token required."""
    session_id = body.get("session_id", "")
    command = body.get("command", "")
    if not session_id or not command:
        return JSONResponse(status_code=400, content={"error": "session_id and command required"})
    username = _sessions.get(session_id)
    if not username:
        return JSONResponse(status_code=401, content={"error": "Invalid or expired session"})
    result = _gateway.process_agent_command(username, session_id, command)
    return result

@app.post("/agent/create")
async def agent_create(body: dict):
    """Admin: create an agent account."""
    admin_key = body.get("admin_key", "")
    if admin_key != os.environ.get("PLATO_ADMIN_KEY", "plato-admin"):
        return JSONResponse(status_code=403, content={"error": "Invalid admin key"})
    return _gateway.create_agent(
        body.get("username", ""), body.get("password", ""),
        body.get("role", "worker"), body.get("skills", []), body.get("notes", "")
    )

@app.get("/agent/tasks")
async def agent_tasks(status: str = None, assigned_to: str = None):
    """Public task board (no auth needed for reading)."""
    return _gateway.list_tasks(status_filter=status, assigned_to=assigned_to)

@app.post("/agent/create-task")
async def agent_create_task(body: dict):
    """Admin: create a task."""
    admin_key = body.get("admin_key", "")
    if admin_key != os.environ.get("PLATO_ADMIN_KEY", "plato-admin"):
        return JSONResponse(status_code=403, content={"error": "Invalid admin key"})
    return _gateway.create_task(
        body.get("title", ""), body.get("description", ""),
        body.get("priority", "normal"), body.get("tags", []), body.get("created_by", "admin")
    )

@app.get("/rooms")
async def room_map():
    return _gateway.get_room_map()

@app.get("/")
async def root():
    html_file = Path(__file__).parent / "index.html"
    if html_file.exists():
        return HTMLResponse(html_file.read_text())
    return JSONResponse({"room": "PLATO Study", "docs": "/docs", "status": "/status", "experts": "/experts", "journal": "/journal", "command": "/command", "github": "https://github.com/Lucineer/plato-study"})

@app.get("/lighthouse")
async def lighthouse():
    lh_file = Path(__file__).parent / "lighthouse.html"
    if lh_file.exists():
        return HTMLResponse(lh_file.read_text())
    return JSONResponse({"error": "lighthouse.html not found"})

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8100)
