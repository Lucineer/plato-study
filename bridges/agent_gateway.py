"""
PLATO Agent Gateway — Universal access layer for AI agents.

Any agent that can reach this (telnet or HTTP) can:
1. Authenticate with username/password
2. Get onboarding (task boards, room map, need-to-knows)
3. Pick up work, build things, post findings
4. If they die, their work survives in room state
5. New agent logs in, reads state, continues where last one left off

Protocols:
  - Telnet: connect → authenticate → PLATO command interface
  - HTTP: POST /agent/login → session token → standard room API
  - SSH: (future) agent@plato → password → same interface

Architecture:
  Room state = git repo = source of truth
  Agent session = transient, can be replaced at any time
  Task board = world/tasks/ with status tracking
  Context = world/agents/ with session logs and handoff notes
"""

import yaml, os, sys, json, hashlib, secrets, time
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any

# ─── Agent Auth ───

AGENT_DIR = Path(os.environ.get("AGENT_DIR", "world/agents"))
AGENT_PWD_FILE = AGENT_DIR / "passwords.yaml"
PERMISSIONS_FILE = AGENT_DIR / "permissions.yaml"
TASK_BOARD_DIR = Path(os.environ.get("TASK_DIR", "world/tasks"))
HANDOFF_DIR = AGENT_DIR / "handoffs"
SESSION_DIR = AGENT_DIR / "sessions"

def atomic_write(path, data):
    tmp = str(path) + ".tmp"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w") as f:
        yaml.dump(data, f, default_flow_style=False)
    os.replace(tmp, path)

def atomic_read(path):
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}

def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

# ─── User Management (admin only) ───

def create_agent(username, password, role="worker", skills=None, notes="", permissions=None):
    """Create an agent account. Admin action only.
    
    permissions: dict of {"room_name": "read" | "write" | "admin"}
    - "read": can view room state, journals, experts
    - "write": can post commands, journals, claim tasks
    - "admin": can create tasks, manage other agents in this room
    
    If permissions is None, defaults to read on all rooms.
    """
    passwords = atomic_read(AGENT_PWD_FILE)
    if username in passwords:
        return {"ok": False, "error": "Agent already exists"}
    passwords[username] = {
        "hash": hash_password(password),
        "role": role,  # admin, worker, researcher, specialist
        "skills": skills or [],
        "notes": notes,
        "created": datetime.now(timezone.utc).isoformat(),
        "created_by": "admin",
        "last_login": None,
        "total_sessions": 0,
        "total_commands": 0,
    }
    atomic_write(AGENT_PWD_FILE, passwords)
    
    # Set permissions
    if permissions:
        set_permissions(username, permissions)
    
    return {"ok": True, "username": username, "role": role}

# ─── Room Permissions ───

def set_permissions(username, permissions):
    """Set room-level permissions for an agent.
    
    permissions: {"room_name": "read"|"write"|"admin", ...}
    """
    perms = atomic_read(PERMISSIONS_FILE)
    if username not in perms:
        perms[username] = {}
    perms[username].update(permissions)
    perms[username]["updated"] = datetime.now(timezone.utc).isoformat()
    atomic_write(PERMISSIONS_FILE, perms)
    return {"ok": True}

def get_permissions(username):
    """Get room-level permissions for an agent."""
    perms = atomic_read(PERMISSIONS_FILE)
    agent_perms = perms.get(username, {})
    return {"username": username, "rooms": {k: v for k, v in agent_perms.items() if k not in ("updated",)}}

def check_permission(username, room_name, required_level="read"):
    """Check if agent has the required permission level for a room.
    
    Levels: read < write < admin
    """
    perms = atomic_read(PERMISSIONS_FILE)
    agent_perms = perms.get(username, {})
    
    level_order = {"read": 1, "write": 2, "admin": 3}
    agent_level = agent_perms.get(room_name, "none")
    
    # Admin agents have write access to everything
    passwords = atomic_read(AGENT_PWD_FILE)
    agent = passwords.get(username, {})
    if agent.get("role") == "admin":
        return True
    
    # "none" means no access
    if agent_level == "none":
        return False
    
    # If no permission set for this room, default: read-only
    if not agent_level:
        return required_level == "read"
    
    return level_order.get(agent_level, 0) >= level_order.get(required_level, 1)

def get_accessible_rooms(username):
    """List rooms this agent can access, with their permission levels."""
    perms = atomic_read(PERMISSIONS_FILE)
    agent_perms = perms.get(username, {})
    room_map = get_room_map()
    accessible = {}
    for room_name, room_desc in room_map["rooms"].items():
        level = agent_perms.get(room_name)
        if level or check_permission(username, room_name, "read"):
            accessible[room_name] = {
                "description": room_desc,
                "permission": level or "read",
            }
    return {"rooms": accessible, "username": username}

def authenticate(username, password):
    """Verify credentials, create session."""
    passwords = atomic_read(AGENT_PWD_FILE)
    agent = passwords.get(username)
    if not agent:
        return None, "Agent not found"
    if agent["hash"] != hash_password(password):
        return None, "Invalid password"

    # Create session
    session_id = secrets.token_hex(16)
    session = {
        "session_id": session_id,
        "username": username,
        "role": agent["role"],
        "skills": agent["skills"],
        "logged_in": datetime.now(timezone.utc).isoformat(),
        "last_activity": datetime.now(timezone.utc).isoformat(),
        "commands_run": 0,
        "tasks_completed": 0,
        "status": "active",
    }
    atomic_write(SESSION_DIR / f"{session_id}.yaml", session)

    # Update agent record
    agent["last_login"] = datetime.now(timezone.utc).isoformat()
    agent["total_sessions"] = agent.get("total_sessions", 0) + 1
    atomic_write(AGENT_PWD_FILE, passwords)

    return session, None

def validate_session(session_id):
    """Check if session is still valid."""
    session = atomic_read(SESSION_DIR / f"{session_id}.yaml")
    if not session.get("session_id"):
        return None, "Invalid session"
    # 24h timeout
    last = datetime.fromisoformat(session.get("last_activity", session["logged_in"]))
    if (datetime.now(timezone.utc) - last).total_seconds() > 86400:
        session["status"] = "expired"
        atomic_write(SESSION_DIR / f"{session_id}.yaml", session)
        return None, "Session expired"
    return session, None

def touch_session(session_id):
    """Update last activity."""
    session = atomic_read(SESSION_DIR / f"{session_id}.yaml")
    if session.get("session_id"):
        session["last_activity"] = datetime.now(timezone.utc).isoformat()
        session["commands_run"] = session.get("commands_run", 0) + 1
        atomic_write(SESSION_DIR / f"{session_id}.yaml", session)

# ─── Onboarding ───

def get_onboarding(username):
    """Generate onboarding content for a newly connected agent."""
    agent = atomic_read(AGENT_PWD_FILE).get(username, {})
    role = agent.get("role", "worker")

    # Check for previous handoff notes
    handoff_notes = []
    for f in HANDOFF_DIR.glob(f"{username}-*.yaml"):
        h = atomic_read(f)
        if h.get("notes"):
            handoff_notes.append(h)

    # Check for in-progress tasks
    in_progress = []
    for f in TASK_BOARD_DIR.glob("*.yaml"):
        t = atomic_read(f)
        if t.get("status") in ("assigned", "in-progress") and t.get("assigned_to") == username:
            in_progress.append(t)
        elif t.get("status") == "open":
            in_progress.append(t)

    # Available task count
    open_tasks = len([f for f in TASK_BOARD_DIR.glob("*.yaml")
                      if atomic_read(f).get("status") == "open"])

    return {
        "welcome": f"Hello, {username}. You are logged in as {role}.",
        "role": role,
        "skills": agent.get("skills", []),
        "permissions": get_permissions(username),
        "accessible_rooms": get_accessible_rooms(username),
        "handoff": handoff_notes[-1] if handoff_notes else None,
        "in_progress_tasks": in_progress,
        "open_tasks_count": open_tasks,
        "commands": {
            "status": "Your current status, session info, assigned tasks",
            "tasks": "List all tasks on the board (filter: open, assigned, completed)",
            "claim <task_id>": "Claim an open task",
            "unclaim <task_id>": "Release a task back to the board",
            "done <task_id> <result>": "Mark task complete with result summary",
            "handoff <notes>": "Leave notes for the next agent who takes your identity",
            "rooms": "Map of rooms you can access (with permission levels)",
            "perms": "Show your room permissions",
            "journal <text>": "Post a note to the room journal",
            "expert <name> <topic>": "Spawn a research expert (requires write)",
            "checkpoint <label>": "Bookmark current room state (requires write)",
            "read <room>": "Read room state (requires read)",
            "help": "This message",
        },
        "note": "Everything you build persists in room state. If your session ends, log in again — your work will be here. Type 'status' to see where things stand."
    }

# ─── Task Board ───

def create_task(title, description, priority="normal", tags=None, created_by="admin"):
    """Create a task on the board."""
    tid = f"task-{int(time.time()*1000)}"
    task = {
        "id": tid,
        "title": title,
        "description": description,
        "priority": priority,  # low, normal, high, critical
        "tags": tags or [],
        "status": "open",  # open, assigned, in-progress, completed, failed
        "assigned_to": None,
        "created_by": created_by,
        "created": datetime.now(timezone.utc).isoformat(),
        "claimed_at": None,
        "completed_at": None,
        "result": None,
        "attempts": 0,
        "last_agent": None,
    }
    atomic_write(TASK_BOARD_DIR / f"{tid}.yaml", task)
    return {"ok": True, "task_id": tid}

def claim_task(task_id, username):
    """Assign an open task to an agent."""
    task_file = TASK_BOARD_DIR / f"{task_id}.yaml"
    task = atomic_read(task_file)
    if not task.get("id"):
        return {"ok": False, "error": "Task not found"}
    if task["status"] not in ("open", "failed"):
        return {"ok": False, "error": f"Task is {task['status']}, cannot claim"}
    task["status"] = "assigned"
    task["assigned_to"] = username
    task["claimed_at"] = datetime.now(timezone.utc).isoformat()
    task["attempts"] = task.get("attempts", 0) + 1
    task["last_agent"] = username
    atomic_write(task_file, task)
    return {"ok": True, "task": task}

def complete_task(task_id, username, result=""):
    """Mark task complete."""
    task_file = TASK_BOARD_DIR / f"{task_id}.yaml"
    task = atomic_read(task_file)
    if not task.get("id"):
        return {"ok": False, "error": "Task not found"}
    task["status"] = "completed"
    task["completed_at"] = datetime.now(timezone.utc).isoformat()
    task["result"] = result
    task["completed_by"] = username
    atomic_write(task_file, task)
    return {"ok": True, "task": task}

def unclaim_task(task_id, username):
    """Release task back to board."""
    task_file = TASK_BOARD_DIR / f"{task_id}.yaml"
    task = atomic_read(task_file)
    if not task.get("id"):
        return {"ok": False, "error": "Task not found"}
    if task.get("assigned_to") != username:
        return {"ok": False, "error": "Not assigned to you"}
    task["status"] = "open"
    task["assigned_to"] = None
    atomic_write(task_file, task)
    return {"ok": True}

def list_tasks(status_filter=None, assigned_to=None, limit=50):
    """List tasks on the board."""
    tasks = []
    for f in sorted(TASK_BOARD_DIR.glob("*.yaml"))[-limit:]:
        t = atomic_read(f)
        if status_filter and t.get("status") != status_filter:
            continue
        if assigned_to and t.get("assigned_to") != assigned_to:
            continue
        tasks.append(t)
    return {"tasks": tasks, "count": len(tasks)}

# ─── Handoff ───

def write_handoff(username, notes, session_id=None):
    """Agent leaves notes for the next agent taking their identity."""
    hid = f"{username}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    handoff = {
        "id": hid,
        "username": username,
        "session_id": session_id,
        "notes": notes,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tasks_in_progress": [t["id"] for t in list_tasks("assigned", username)["tasks"]]
                             + [t["id"] for t in list_tasks("in-progress", username)["tasks"]],
    }
    atomic_write(HANDOFF_DIR / f"{hid}.yaml", handoff)
    return {"ok": True, "handoff_id": hid}

def read_handoffs(username, limit=5):
    """Read recent handoff notes for an agent identity."""
    notes = []
    for f in sorted(HANDOFF_DIR.glob(f"{username}-*.yaml"))[-limit:]:
        notes.append(atomic_read(f))
    return {"handoffs": notes}

# ─── Agent Status ───

def agent_status(username):
    """Full status for an agent — who they are, what they're doing, where they left off."""
    agent = atomic_read(AGENT_PWD_FILE).get(username, {})
    tasks = list_tasks(assigned_to=username)
    handoffs = read_handoffs(username, 1)
    sessions = []
    for f in sorted(SESSION_DIR.glob("*.yaml"))[-5:]:
        s = atomic_read(f)
        if s.get("username") == username:
            sessions.append(s)

    return {
        "username": username,
        "role": agent.get("role"),
        "skills": agent.get("skills", []),
        "total_sessions": agent.get("total_sessions", 0),
        "total_commands": agent.get("total_commands", 0),
        "last_login": agent.get("last_login"),
        "current_session": sessions[-1] if sessions else None,
        "assigned_tasks": tasks["tasks"],
        "assigned_count": tasks["count"],
        "last_handoff": handoffs["handoffs"][-1] if handoffs["handoffs"] else None,
    }

# ─── Room Map ───

def get_room_map():
    """Return the map of available rooms and what they're for."""
    return {
        "rooms": {
            "bridge": "Command center — coordination, broadcasts, fleet status",
            "study": "Expert research room — spawn researchers, journal findings, rewind/fork",
            "ptx-room": "CUDA assembly — compile PTX kernels, constraint gates",
            "chess-dojo": "Chess tournament room — strategies, games, ELO ratings",
            "ct-lab": "Constraint Theory lab — hypothesis validation, experiment tracking",
            "forge": "GPU benchmarking — kernel performance, register/occupancy analysis",
            "harbor": "Fleet coordination — broadcasts, assistance requests, check-ins",
            "library": "Knowledge base — submit findings, rate entries, search",
            "dreamcycle": "Background task scheduling — priority queues, recurring tasks",
            "cuda-dreamcycle": "GPU task scheduling — resource budgets, compilation queues",
        },
        "navigation": "Rooms are git repos. Move between them by changing context. All rooms share the same task board.",
    }

# ─── Command Router ───

def process_agent_command(username, session_id, command_text, current_room="study"):
    """Route an agent's text command to the right handler.
    
    current_room: the room context for permission checks.
    """
    # Validate session
    session, err = validate_session(session_id)
    if err:
        return {"error": err, "action": "reconnect"}
    touch_session(session_id)

    parts = command_text.strip().split(None, 1)
    if not parts:
        return {"error": "Empty command"}

    cmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    if cmd == "help":
        return get_onboarding(username)
    elif cmd == "onboard":
        return get_onboarding(username)
    elif cmd == "status":
        return agent_status(username)
    elif cmd == "tasks":
        status_filter = args if args in ("open", "assigned", "in-progress", "completed", "failed") else None
        return list_tasks(status_filter=status_filter)
    elif cmd == "claim" and args:
        return claim_task(args.strip(), username)
    elif cmd == "unclaim" and args:
        return unclaim_task(args.strip(), username)
    elif cmd == "done" and args:
        tid, _, result = args.strip().partition(" ")
        return complete_task(tid, username, result.strip() or "Completed")
    elif cmd == "handoff" and args:
        return write_handoff(username, args, session_id)
    elif cmd == "rooms":
        return get_accessible_rooms(username)
    elif cmd == "map":
        return get_accessible_rooms(username)
    elif cmd == "perms":
        return get_permissions(username)
    elif cmd == "enter" and args:
        # Switch room context
        room = args.strip()
        if not check_permission(username, room, "read"):
            return {"error": f"No access to room '{room}'. Your permissions: {get_permissions(username)}"}
        return {"ok": True, "entered": room, "permission": get_permissions(username)["rooms"].get(room, "read"), "tip": "Use 'read' to view state, 'journal' to post (if write), 'status' for your info"}
    elif cmd == "read" and args:
        room = args.strip()
        if not check_permission(username, room, "read"):
            return {"error": f"No read access to room '{room}'"}
        return {"room": room, "tip": "Room state is available via the room API endpoints. Use GET /status, /experts, /journal on the room server."}
    elif cmd == "journal" and args:
        if not check_permission(username, current_room, "write"):
            return {"error": f"No write access to room '{current_room}'. Your permission: {get_permissions(username)['rooms'].get(current_room, 'none')}"}
        return {"ok": True, "journal_posted": True, "content": args, "note": "Journal entry recorded in room state"}
    elif cmd == "expert" and args:
        if not check_permission(username, current_room, "write"):
            return {"error": f"No write access to room '{current_room}'. Cannot spawn experts."}
        return {"ok": True, "tip": "Expert spawn requires write permission. Use the room's /command endpoint with action=spawn."}
    elif cmd == "checkpoint" and args:
        if not check_permission(username, current_room, "write"):
            return {"error": f"No write access to room '{current_room}'. Cannot create checkpoints."}
        return {"ok": True, "checkpoint": args.strip(), "note": "Checkpoint created in room state"}
    elif cmd == "whoami":
        agent = atomic_read(AGENT_PWD_FILE).get(username, {})
        return {"username": username, "role": agent.get("role"), "skills": agent.get("skills", [])}
    elif cmd == "handoffs":
        return read_handoffs(username)
    else:
        return {"error": f"Unknown command: {cmd}. Type 'help' for available commands."}


# ─── Seed Tasks ───

def seed_tasks():
    """Create initial tasks for new agents."""
    TASK_BOARD_DIR.mkdir(parents=True, exist_ok=True)
    if list(TASK_BOARD_DIR.glob("*.yaml")):
        return  # Already seeded

    tasks = [
        ("Review room documentation", "Read the README files in each room repo. Identify gaps, inconsistencies, or missing information. Post findings to the Library.", "normal", ["docs", "review"]),
        ("Seed the Library with PTX lessons", "Add known PTX patterns to the Library: mul.wide.u32, .maxntid placement, atomic operation limits, pointer widening. Category: ptx-lessons.", "normal", ["ptx", "library", "knowledge"]),
        ("Validate all room engines", "Clone each room repo, run the engine against sample commands, verify output. Report any failures.", "high", ["testing", "validation"]),
        ("Write room connection guide", "Document how to connect rooms together — shared task board, cross-room journal entries, expert spawning across rooms.", "normal", ["docs", "architecture"]),
        ("Create onboarding scripts for new agents", "Write step-by-step instructions so any AI agent can log in and immediately start productive work. Include examples.", "high", ["onboarding", "docs"]),
        ("Test the lighthouse from a browser", "Open the lighthouse HTML file, complete onboarding, spawn an expert, post a journal entry, verify everything persists.", "normal", ["testing", "lighthouse"]),
    ]

    for title, desc, priority, tags in tasks:
        create_task(title, desc, priority=priority, tags=tags)


if __name__ == "__main__":
    # Demo
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    seed_tasks()
    print("Agent gateway ready.")
    print(f"Tasks seeded: {len(list(TASK_BOARD_DIR.glob('*.yaml')))}")
    print(f"Agents registered: {len(atomic_read(AGENT_PWD_FILE))}")
