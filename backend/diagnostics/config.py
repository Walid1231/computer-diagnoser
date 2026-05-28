"""
Configuration Manager
Handles saving/loading API keys and user preferences.
Stored locally in the user's home directory.
"""

import os
import json

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".diagnoser_config.json")

DEFAULT_CONFIG = {
    "ai_provider": "",          # Display name: "Gemini", "Claude", "Groq", "My Local LLM", etc.
    "api_key": "",              # API key (can be empty for local LLMs)
    "endpoint_url": "",         # Full API endpoint URL
    "model_name": "",           # Model name to use
    "api_format": "openai",     # "openai" (works with 90% of LLMs), "gemini", "claude"
    "ai_enabled": False,
}

# Pre-built presets for common providers
PRESETS = {
    "gemini": {
        "label": "Google Gemini",
        "endpoint_url": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
        "model_name": "gemini-2.0-flash",
        "api_format": "gemini",
        "needs_key": True,
    },
    "claude": {
        "label": "Anthropic Claude",
        "endpoint_url": "https://api.anthropic.com/v1/messages",
        "model_name": "claude-sonnet-4-20250514",
        "api_format": "claude",
        "needs_key": True,
    },
    "openai": {
        "label": "OpenAI",
        "endpoint_url": "https://api.openai.com/v1/chat/completions",
        "model_name": "gpt-4o-mini",
        "api_format": "openai",
        "needs_key": True,
    },
    "groq": {
        "label": "Groq",
        "endpoint_url": "https://api.groq.com/openai/v1/chat/completions",
        "model_name": "llama-3.3-70b-versatile",
        "api_format": "openai",
        "needs_key": True,
    },
    "deepseek": {
        "label": "DeepSeek",
        "endpoint_url": "https://api.deepseek.com/v1/chat/completions",
        "model_name": "deepseek-chat",
        "api_format": "openai",
        "needs_key": True,
    },
    "together": {
        "label": "Together AI",
        "endpoint_url": "https://api.together.xyz/v1/chat/completions",
        "model_name": "meta-llama/Llama-3-70b-chat-hf",
        "api_format": "openai",
        "needs_key": True,
    },
    "mistral": {
        "label": "Mistral AI",
        "endpoint_url": "https://api.mistral.ai/v1/chat/completions",
        "model_name": "mistral-small-latest",
        "api_format": "openai",
        "needs_key": True,
    },
    "ollama": {
        "label": "Ollama (Local)",
        "endpoint_url": "http://localhost:11434/v1/chat/completions",
        "model_name": "llama3",
        "api_format": "openai",
        "needs_key": False,
    },
    "lmstudio": {
        "label": "LM Studio (Local)",
        "endpoint_url": "http://localhost:1234/v1/chat/completions",
        "model_name": "local-model",
        "api_format": "openai",
        "needs_key": False,
    },
    "custom": {
        "label": "Custom API",
        "endpoint_url": "",
        "model_name": "",
        "api_format": "openai",
        "needs_key": True,
    },
}


def load_config() -> dict:
    """Load config from disk, or return defaults."""
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r") as f:
                saved = json.load(f)
                config = {**DEFAULT_CONFIG, **saved}
                return config
    except (json.JSONDecodeError, OSError):
        pass
    return dict(DEFAULT_CONFIG)


def save_config(config: dict) -> bool:
    """Save config to disk."""
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)
        return True
    except OSError:
        return False


def get_presets() -> dict:
    """Return all available presets."""
    return PRESETS


def is_ai_enabled() -> bool:
    """Check if AI chat is configured and enabled."""
    config = load_config()
    return bool(
        config.get("ai_enabled")
        and config.get("endpoint_url")
        and config.get("api_format")
    )
