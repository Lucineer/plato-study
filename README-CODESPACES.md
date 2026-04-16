# PLATO Study — Codespaces Quick Start

## Open in Codespaces
1. Go to https://github.com/Lucineer/plato-study
2. Click green "Code" button → "Codespaces" tab → "Create codespace on main"

## The server starts automatically
Wait ~30 seconds after the terminal shows `pip install` completing. You'll see:
```
INFO:     Uvicorn running on http://0.0.0.0:8100
```

## Try it
Open the forwarded port 8100 (Codespaces shows a notification with the URL, or click "Ports" tab → 8100 → open icon).

### Interactive API docs
Go to `{your-codespace-url}/docs` — full Swagger UI.

### Quick commands in the terminal:
```bash
# Check status
curl localhost:8100/status

# List experts
curl localhost:8100/experts

# Spawn an expert
curl -X POST localhost:8100/command -H "Content-Type: application/json" -d '{
  "agent": "casey",
  "action": "spawn",
  "expert": "research-assistant",
  "topic": "test research",
  "brief": "Explore the room system",
  "model": "deepseek-chat",
  "budget_tokens": 10000,
  "max_rounds": 5
}'

# Post a journal entry
curl -X POST localhost:8100/command -H "Content-Type: application/json" -d '{
  "agent": "research-assistant",
  "action": "journal",
  "expert_id": "research-assistant-20260416-155608",
  "type": "finding",
  "content": "This room works from Codespaces!"
}'

# Check journal
curl localhost:8100/journal
```
