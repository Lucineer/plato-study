#!/usr/bin/env python3
"""PLATO Study — expert research room. Spawn experts on branches, monitor, rewind, fork."""

import yaml, os, subprocess, re
from pathlib import Path
from datetime import datetime, timezone
import fcntl

WORLD_DIR = Path(os.environ.get("WORLD_DIR", "world"))
EXPERTS_DIR = WORLD_DIR / "experts"
BRIEFS_DIR = WORLD_DIR / "briefs"
JOURNALS_DIR = WORLD_DIR / "journals"
COMMANDS_DIR = WORLD_DIR / "commands"
ROOMS_DIR = WORLD_DIR / "rooms"
LOGS_DIR = WORLD_DIR / "logs"
MAX_TURNS = 20

def log(level, msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] [{level}] {msg}", flush=True)

def atomic_write(path, data):
    tmp = str(path) + ".tmp"
    with open(tmp, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yaml.dump(data, f, default_flow_style=False)
        fcntl.flock(f, fcntl.LOCK_UN)
    os.replace(tmp, path)

def atomic_read(path):
    try:
        with open(path) as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            d = yaml.safe_load(f)
            fcntl.flock(f, fcntl.LOCK_UN)
            return d or {}
    except FileNotFoundError:
        return {}

def run_git(args, cwd=None):
    """Run git command, return stdout."""
    base = cwd or WORLD_DIR
    try:
        r = subprocess.run(["git"] + args, capture_output=True, text=True, timeout=30, cwd=base)
        return r.stdout.strip(), r.returncode
    except Exception as e:
        return str(e), 1

def process_spawn(cmd, agent):
    """Spawn an expert on a new branch."""
    expert_name = cmd.get("expert", "")
    topic = cmd.get("topic", "")
    brief = cmd.get("brief", "")
    model = cmd.get("model", "deepseek-chat")
    budget_tokens = int(cmd.get("budget_tokens", 100000))
    max_rounds = int(cmd.get("max_rounds", 20))

    if not expert_name or not topic:
        return {"passed": False, "error": "Missing expert and topic"}

    # Validate expert name (safe for git branches)
    if not re.match(r'^[a-zA-Z0-9_-]+$', expert_name):
        return {"passed": False, "error": "Expert name must be alphanumeric/hyphen/underscore"}

    # Validate model is in allowed list
    allowed_models = [
        "deepseek-chat", "deepseek-reasoner", "glm-5-turbo", "glm-5.1",
        "qwen3-32b", "qwen3.5-397b", "nemotron-120b", "seed-2.0-pro",
        "phi-4", "hermes-405b", "hermes-70b", "gpt-oss-120b",
        "seed-oss-36b", "kimi-k2",
    ]
    if model not in allowed_models:
        return {"passed": False, "error": f"Model not allowed. Options: {allowed_models}"}

    eid = f"{expert_name}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    expert = {
        "id": eid,
        "name": expert_name,
        "topic": topic,
        "brief": brief,
        "model": model,
        "budget_tokens": budget_tokens,
        "tokens_used": 0,
        "max_rounds": max_rounds,
        "rounds_completed": 0,
        "status": "spawned",
        "branch": f"expert/{expert_name}",
        "spawner": agent,
        "forked_from": cmd.get("fork_from_sha"),
        "created": datetime.now(timezone.utc).isoformat(),
        "last_activity": None,
        "checkpoints": [],
    }
    atomic_write(EXPERTS_DIR / f"{eid}.yaml", expert)

    # Save brief as a file the expert will read
    brief_file = BRIEFS_DIR / f"{eid}.md"
    brief_file.write_text(f"# Research Brief: {topic}\n\nExpert: {expert_name}\nModel: {model}\nBudget: {budget_tokens} tokens\nMax rounds: {max_rounds}\n\n---\n\n{brief}\n")

    room = atomic_read(ROOMS_DIR / "study.yaml")
    room.setdefault("stats", {})
    room["stats"]["experts_spawned"] = room["stats"].get("experts_spawned", 0) + 1
    atomic_write(ROOMS_DIR / "study.yaml", room)

    log("INFO", f"Expert '{expert_name}' spawned for '{topic}' by {agent} (model={model}, budget={budget_tokens})")
    return {"passed": True, "expert_id": eid, "branch": expert["branch"], "brief_file": str(brief_file)}

def process_checkpoint(cmd, agent):
    """Save a checkpoint (bookmark) at current state."""
    eid = cmd.get("expert_id")
    label = cmd.get("label", "")
    note = cmd.get("note", "")

    if not eid:
        return {"passed": False, "error": "Missing expert_id"}

    expert = atomic_read(EXPERTS_DIR / f"{eid}.yaml")
    if not expert.get("id"):
        return {"passed": False, "error": "Expert not found"}

    # Get current HEAD sha
    sha, rc = run_git(["rev-parse", "HEAD"])
    if rc != 0:
        return {"passed": False, "error": "Not a git repo or no commits"}

    # Save commit message as context
    msg, _ = run_git(["log", "-1", "--format=%s"])
    files_changed, _ = run_git(["diff", "--name-only", "HEAD~1", "HEAD"]) if rc == 0 else ""

    checkpoint = {
        "sha": sha,
        "label": label or f"checkpoint-{len(expert.get('checkpoints', [])) + 1}",
        "note": note,
        "message": msg,
        "files": files_changed.split("\n") if files_changed else [],
        "created": datetime.now(timezone.utc).isoformat(),
        "saved_by": agent,
    }
    expert.setdefault("checkpoints", []).append(checkpoint)
    expert["last_activity"] = datetime.now(timezone.utc).isoformat()
    atomic_write(EXPERTS_DIR / f"{eid}.yaml", expert)

    log("INFO", f"Checkpoint '{checkpoint['label']}' at {sha[:8]} for expert {eid}")
    return {"passed": True, "sha": sha, "label": checkpoint["label"]}

def process_rewind(cmd, agent):
    """Rewind expert to a checkpoint (checkout sha, keep branch)."""
    eid = cmd.get("expert_id")
    target_sha = cmd.get("sha") or cmd.get("checkpoint_label")

    if not eid or not target_sha:
        return {"passed": False, "error": "Missing expert_id and sha/label"}

    expert = atomic_read(EXPERTS_DIR / f"{eid}.yaml")
    if not expert.get("id"):
        return {"passed": False, "error": "Expert not found"}

    # If label, find matching checkpoint
    if len(target_sha) < 12:  # It's a label
        found = None
        for cp in expert.get("checkpoints", []):
            if cp["label"] == target_sha:
                found = cp["sha"]
                break
        if not found:
            return {"passed": False, "error": f"Checkpoint '{target_sha}' not found"}
        target_sha = found

    # Checkout the target sha on the expert's branch
    branch = expert["branch"]
    _, rc = run_git(["checkout", branch])
    if rc != 0:
        run_git(["checkout", "-b", branch])
    out, rc = run_git(["reset", "--hard", target_sha])
    if rc != 0:
        return {"passed": False, "error": f"Git reset failed: {out}"}

    expert["status"] = "rewound"
    expert["last_activity"] = datetime.now(timezone.utc).isoformat()
    atomic_write(EXPERTS_DIR / f"{eid}.yaml", expert)

    log("INFO", f"Expert {eid} rewound to {target_sha[:8]} by {agent}")
    return {"passed": True, "rewound_to": target_sha, "branch": branch}

def process_fork(cmd, agent):
    """Fork expert from a checkpoint into a new attempt."""
    eid = cmd.get("expert_id")
    target_sha = cmd.get("sha") or cmd.get("checkpoint_label")
    new_name = cmd.get("new_expert_name", "")

    if not eid or not target_sha or not new_name:
        return {"passed": False, "error": "Missing expert_id, sha/label, new_expert_name"}

    expert = atomic_read(EXPERTS_DIR / f"{eid}.yaml")
    if not expert.get("id"):
        return {"passed": False, "error": "Expert not found"}

    if len(target_sha) < 12:
        found = None
        for cp in expert.get("checkpoints", []):
            if cp["label"] == target_sha:
                found = cp["sha"]
                break
        if not found:
            return {"passed": False, "error": f"Checkpoint '{target_sha}' not found"}
        target_sha = found

    # Create new branch from the target sha
    new_branch = f"expert/{new_name}"
    out, rc = run_git(["branch", new_branch, target_sha])
    if rc != 0:
        return {"passed": False, "error": f"Git branch failed: {out}"}

    # Create new expert record inheriting config
    new_eid = f"{new_name}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    new_expert = {
        "id": new_eid,
        "name": new_name,
        "topic": expert["topic"],
        "brief": expert["brief"],
        "model": expert["model"],
        "budget_tokens": expert["budget_tokens"],
        "tokens_used": 0,
        "max_rounds": expert["max_rounds"],
        "rounds_completed": 0,
        "status": "spawned",
        "branch": new_branch,
        "spawner": agent,
        "forked_from": target_sha,
        "forked_from_expert": eid,
        "created": datetime.now(timezone.utc).isoformat(),
        "last_activity": None,
        "checkpoints": [],
    }
    atomic_write(EXPERTS_DIR / f"{new_eid}.yaml", new_expert)

    log("INFO", f"Expert '{new_name}' forked from {eid} at {target_sha[:8]} by {agent}")
    return {"passed": True, "expert_id": new_eid, "branch": new_branch, "forked_from": target_sha}

def process_journal(cmd, agent):
    """Expert posts a journal entry (research log / thinking out loud)."""
    eid = cmd.get("expert_id")
    content = cmd.get("content", "")
    entry_type = cmd.get("type", "note")  # note, finding, question, redirect, dead-end

    if not eid or not content:
        return {"passed": False, "error": "Missing expert_id and content"}
    if len(content) > 50000:
        return {"passed": False, "error": "Journal entry too long (max 50K chars)"}

    jid = f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')}"
    journal = {
        "id": jid,
        "expert_id": eid,
        "type": entry_type,
        "content": content,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write(JOURNALS_DIR / f"{jid}.yaml", journal)

    # Update expert activity
    expert = atomic_read(EXPERTS_DIR / f"{eid}.yaml")
    if expert.get("id"):
        expert["last_activity"] = datetime.now(timezone.utc).isoformat()
        expert["rounds_completed"] = expert.get("rounds_completed", 0) + 1
        atomic_write(EXPERTS_DIR / f"{eid}.yaml", expert)

    log("INFO", f"Journal [{entry_type}] from {eid}: {content[:80]}...")
    return {"passed": True, "journal_id": jid}

def process_status(cmd, agent):
    """Get study room overview."""
    room = atomic_read(ROOMS_DIR / "study.yaml")
    experts = []
    for f in EXPERTS_DIR.glob("*.yaml"):
        e = atomic_read(f)
        if e.get("id"):
            experts.append({
                "id": e["id"], "name": e["name"], "topic": e["topic"],
                "model": e["model"], "status": e["status"],
                "rounds": e.get("rounds_completed", 0), "max_rounds": e.get("max_rounds", 0),
                "tokens": e.get("tokens_used", 0), "budget": e.get("budget_tokens", 0),
                "checkpoints": len(e.get("checkpoints", [])),
                "forked_from": e.get("forked_from"),
            })

    active = [e for e in experts if e["status"] in ("spawned", "working")]
    return {"passed": True, "stats": room.get("stats", {}),
            "active_experts": len(active), "total_experts": len(experts),
            "experts": experts}

def process_turns():
    for d in [COMMANDS_DIR, EXPERTS_DIR, BRIEFS_DIR, JOURNALS_DIR, ROOMS_DIR, LOGS_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    if not (ROOMS_DIR / "study.yaml").exists():
        atomic_write(ROOMS_DIR / "study.yaml", {"name": "PLATO Study", "stats": {"experts_spawned": 0}})
    commands = sorted(COMMANDS_DIR.glob("*.yaml"))
    if not commands:
        return
    log("INFO", f"Processing {len(commands)} commands")
    counts = {}
    for cp in commands:
        cmd = atomic_read(cp)
        if not cmd:
            cp.unlink(); continue
        agent = cmd.get("agent", "unknown")
        counts[agent] = counts.get(agent, 0) + 1
        if counts[agent] > MAX_TURNS:
            cp.unlink(); continue
        action = cmd.get("action")
        if action == "spawn":
            r = process_spawn(cmd, agent)
        elif action == "checkpoint":
            r = process_checkpoint(cmd, agent)
        elif action == "rewind":
            r = process_rewind(cmd, agent)
        elif action == "fork":
            r = process_fork(cmd, agent)
        elif action == "journal":
            r = process_journal(cmd, agent)
        elif action == "status":
            r = process_status(cmd, agent)
        else:
            r = {"passed": False, "error": f"Unknown: {action}"}
        atomic_write(LOGS_DIR / f"turn-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')}.yaml",
                     {"agent": agent, "action": action, "result": r,
                      "timestamp": datetime.now(timezone.utc).isoformat()})
        cp.unlink()
    log("INFO", f"Turn done")

if __name__ == "__main__":
    process_turns()
