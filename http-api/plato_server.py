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

def read_yaml(p):
    try: return yaml.safe_load(open(p)) or {}
    except: return {}

def write_yaml(p, d):
    t = str(p)+".tmp"
    open(t,"w").write(yaml.dump(d, default_flow_style=False))
    os.replace(t, p)

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
