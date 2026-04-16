#!/usr/bin/env python3
"""PLATO Onboarding — configure your PLATO instance.

Like OpenClaw's BOOTSTRAP.md but interactive. Sets up:
- API keys for LLM providers
- Base URLs (for custom endpoints)
- Model selection per provider
- Admin credentials
- Communication channels (future: telegram, discord)

Usage: python3 plato_onboard.py
"""

import json, os, sys, getpass
from pathlib import Path

CONFIG_DIR = Path.home() / ".plato"
CONFIG_FILE = CONFIG_DIR / "config.json"
ROOMS_DIR = CONFIG_DIR / "rooms"

BANNER = """
╔══════════════════════════════════════════╗
║          P L A T O   S E T U P          ║
║    Expert Research Room Platform        ║
╚══════════════════════════════════════════╝

Welcome. Let's configure your PLATO instance.

This sets up API keys and models so your experts
can actually think. You can always edit
~/.plato/config.json later.

"""

PROVIDERS = {
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "key_hint": "sk-...",
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "o1", "o3-mini"],
        "key_hint": "sk-...",
    },
    "anthropic": {
        "name": "Anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "models": ["claude-sonnet-4-20250514", "claude-haiku-4-20250414"],
        "key_hint": "sk-ant-...",
    },
    "siliconflow": {
        "name": "SiliconFlow",
        "base_url": "https://api.siliconflow.com/v1",
        "models": [
            "deepseek-ai/DeepSeek-V3",
            "deepseek-ai/DeepSeek-R1",
            "Qwen/Qwen3-32B",
            "Qwen/Qwen3.5-397B-A17B",
            "ByteDance-Seed/Seed-OSS-36B-Instruct",
            "nvidia/Nemotron-4-340B-Instruct",
        ],
        "key_hint": "sk-...",
    },
    "deepinfra": {
        "name": "DeepInfra",
        "base_url": "https://api.deepinfra.com/v1/openai",
        "models": [
            "meta-llama/Meta-Llama-3.1-405B-Instruct",
            "Qwen/Qwen3-32B",
            "Qwen/Qwen3.5-397B-A17B",
            "microsoft/phi-4",
            "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B",
        ],
        "key_hint": "...",
    },
    "groq": {
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "models": [
            "llama-3.1-8b-instant",
            "llama-3.3-70b-versatile",
            "meta-llama/llama-4-scout-17b-16e-instruct",
            "qwen/qwen3-32b",
        ],
        "key_hint": "gsk_...",
    },
    "google": {
        "name": "Google AI (Gemini)",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "models": ["gemini-2.0-flash", "gemini-2.5-pro"],
        "key_hint": "AIza...",
    },
    "moonshot": {
        "name": "Moonshot (Kimi)",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-128k", "moonshot-v1-32k", "moonshot-v1-8k"],
        "key_hint": "sk-...",
    },
    "custom": {
        "name": "Custom Provider",
        "base_url": "https://",
        "models": [],
        "key_hint": "your-api-key",
    },
}

def ask(prompt, default="", hidden=False):
    try:
        if hidden:
            val = getpass.getpass(f"  {prompt} [{'(hidden)' if not default else default}]: ") or default
        else:
            val = input(f"  {prompt} [{default}]: ") or default
        return val.strip()
    except EOFError:
        return default

def ask_choice(prompt, options, default_idx=0):
    for i, opt in enumerate(options):
        marker = "→" if i == default_idx else " "
        print(f"  {marker} [{i+1}] {opt}")
    while True:
        val = ask(f"  {prompt}", str(default_idx + 1))
        try:
            idx = int(val) - 1
            if 0 <= idx < len(options):
                return options[idx]
        except ValueError:
            pass
        print(f"  Pick 1-{len(options)}")

def ask_yes(prompt, default=True):
    hint = "Y/n" if default else "y/N"
    val = ask(f"  {prompt}", hint)
    if val.lower().startswith("n"):
        return False
    return True

def onboard():
    print(BANNER)

    config = {
        "version": "1.0",
        "admin": {},
        "providers": {},
        "models": [],
        "rooms_dir": str(ROOMS_DIR),
        "server": {
            "host": "0.0.0.0",
            "port": 8100,
        },
    }

    # Admin setup
    print("━━━ Admin Identity ━━━")
    config["admin"]["name"] = ask("Your name", "Captain")
    config["admin"]["email"] = ask("Email (optional)", "")
    print()

    # Provider setup
    print("━━━ API Providers ━━━")
    print("  Add providers for your experts to use.")
    print("  You can skip any you don't have keys for.\n")

    for key, provider in PROVIDERS.items():
        print(f"  ── {provider['name']} ──")
        want = ask_yes(f"  Add {provider['name']}?", False)
        if not want:
            print()
            continue

        api_key = ask("  API key", "", hidden=True)
        if not api_key:
            print("  Skipped (no key).\n")
            continue

        base_url = ask("  Base URL", provider["base_url"])

        entry = {
            "key": api_key,
            "base_url": base_url,
            "models": [],
        }

        # Model selection
        if provider["models"]:
            print(f"\n  Available models for {provider['name']}:")
            selected = []
            for m in provider["models"]:
                if ask_yes(f"    Include {m}?", True):
                    selected.append(m)
            entry["models"] = selected
        else:
            # Custom — ask for model names
            print("\n  Enter model IDs (one per line, empty line to finish):")
            models = []
            while True:
                m = ask("    Model ID", "")
                if not m:
                    break
                models.append(m)
            entry["models"] = models

        config["providers"][key] = entry
        config["models"].extend([f"{key}/{m}" for m in entry["models"]])
        print()

    # Default model
    if config["models"]:
        print("━━━ Default Expert Model ━━━")
        config["default_model"] = ask_choice(
            "Which model should experts use by default?",
            config["models"],
            0
        )
    else:
        print("  ⚠ No providers configured. Experts won't be able to think.")
        print("  You can add providers later in ~/.plato/config.json\n")
        config["default_model"] = None

    # Server config
    print("━━━ Server ━━━")
    config["server"]["port"] = int(ask("  Port", "8100"))
    print()

    # Save
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    ROOMS_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

    print("━━━ Done! ━━━")
    print(f"  Config saved to: {CONFIG_FILE}")
    print(f"  Rooms directory: {ROOMS_DIR}")
    print()
    print("  Next steps:")
    print(f"    1. Clone a room:  git clone https://github.com/Lucineer/plato-study {ROOMS_DIR}/study")
    print(f"    2. Start server:  python3 plato_server.py --rooms-dir {ROOMS_DIR}")
    print(f"    3. Open browser:  http://localhost:{config['server']['port']}")
    print()
    print("  Or re-run this anytime: python3 plato_onboard.py")
    print()

def show_config():
    if not CONFIG_FILE.exists():
        print("  No config found. Run: python3 plato_onboard.py")
        return
    with open(CONFIG_FILE) as f:
        config = json.load(f)
    print(f"  Config: {CONFIG_FILE}")
    print(f"  Admin:  {config.get('admin', {}).get('name', 'unknown')}")
    print(f"  Models: {len(config.get('models', []))} configured")
    for key, p in config.get("providers", {}).items():
        masked = p["key"][:8] + "..." if len(p["key"]) > 8 else "***"
        print(f"    {key}: {masked} ({len(p['models'])} models)")
    print(f"  Default: {config.get('default_model', 'none')}")
    print(f"  Server:  port {config.get('server', {}).get('port', 8100)}")
    print(f"  Rooms:   {ROOMS_DIR}")
    if ROOMS_DIR.exists():
        rooms = [d.name for d in ROOMS_DIR.iterdir() if d.is_dir()]
        print(f"    Installed: {', '.join(rooms) if rooms else 'none'}")

if __name__ == "__main__":
    if "--show" in sys.argv or "--status" in sys.argv:
        show_config()
    elif "--reset" in sys.argv:
        if CONFIG_FILE.exists():
            CONFIG_FILE.unlink()
            print("  Config cleared. Run onboard again.")
    else:
        onboard()
