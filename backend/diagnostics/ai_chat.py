"""
AI Chat Handler — Universal LLM Support
Connects to ANY LLM API (OpenAI-compatible, Gemini, Claude, or custom).
Feeds live system diagnostic data as context for intelligent PC analysis.
"""

import json
import urllib.request
import urllib.error
from diagnostics import storage, system, network, startup
from diagnostics.config import load_config


# ─── SYSTEM CONTEXT BUILDER ──────────────────────────────────

def build_system_context() -> str:
    """Gather all live system data and format it as context for the AI."""
    try:
        sys_info = system.get_system_info()
        cpu = system.get_cpu_info()
        mem = system.get_memory_info()
        drives = storage.get_drive_usage()
        temp = storage.get_temp_files()
        procs = system.get_top_processes(top_n=8)
        battery = system.get_battery_info()
        startup_items = startup.get_startup_programs()
    except Exception as e:
        return f"[Error gathering system data: {e}]"

    context = f"""=== LIVE SYSTEM DATA (Real-time from user's PC) ===

SYSTEM: {sys_info.get('hostname', 'Unknown')} | {sys_info.get('os', 'Unknown')} | {sys_info.get('architecture', '')}
PROCESSOR: {sys_info.get('processor', 'Unknown')}
UPTIME: {sys_info.get('uptime', 'Unknown')}

CPU: {cpu['overall_percent']}% usage | {cpu['physical_cores']} cores / {cpu['logical_cores']} threads | {cpu['current_freq_mhz']} MHz
Per-core: {cpu['per_cpu_percent']}

RAM: {mem['ram']['percent_used']}% used | {mem['ram']['used_display']} / {mem['ram']['total_display']} | Free: {mem['ram']['available_display']}
SWAP: {mem['swap']['percent_used']}% used | {mem['swap']['used_display']} / {mem['swap']['total_display']}

DRIVES:
"""
    for d in drives:
        context += f"  {d['mountpoint']} — {d['percent_used']}% full | Used: {d['used_display']} | Free: {d['free_display']} | Total: {d['total_display']} ({d['fstype']})\n"

    total_temp = sum(t["size_bytes"] for t in temp)
    if total_temp > 0:
        context += f"\nTEMP/CACHE FILES: {storage._format_bytes(total_temp)} total cleanable\n"
        for t in temp:
            context += f"  {t['name']} — {t['size_display']} at {t['path']}\n"

    context += "\nTOP PROCESSES (by memory):\n"
    for p in procs:
        context += f"  {p['name']} (PID {p['pid']}) — {p['memory_display']} RAM, {p['cpu_percent']}% CPU [{p['status']}]\n"

    if battery:
        status = "Charging" if battery.get("power_plugged") else f"{battery.get('time_left', '?')} remaining"
        context += f"\nBATTERY: {battery.get('percent', '?')}% | {status}\n"

    context += f"\nSTARTUP PROGRAMS: {len(startup_items)} items\n"
    for s in startup_items[:10]:
        context += f"  {s['name']} — {s['scope']} ({s['source']})\n"

    return context


SYSTEM_PROMPT = """You are an expert Windows PC diagnostic assistant embedded in a desktop application called "Computer Diagnoser". 

Your role:
- Analyze the user's LIVE system data (provided below) and answer their questions accurately
- Identify problems: low storage, high RAM/CPU usage, too many startup programs, etc.
- Give specific, actionable advice (which files to delete, which programs to close, etc.)
- Be concise but thorough — use bullet points and bold (**text**) for key info
- Use emojis for status indicators: 🔴 critical, 🟡 warning, 🟢 healthy
- If the user asks to delete/clean something, explain what it will do and confirm it's safe

You have access to REAL-TIME data from their PC. The data is refreshed each time they ask a question.
Keep explanations simple and practical — the user is not a developer."""


# ─── UNIVERSAL API CALLERS ────────────────────────────────────

def call_openai_compatible(endpoint_url: str, api_key: str, model: str, question: str, context: str) -> str:
    """
    Call any OpenAI-compatible API.
    Works with: OpenAI, Groq, Together, Mistral, DeepSeek, Fireworks,
    Perplexity, Ollama, LM Studio, vLLM, and hundreds more.
    """
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": f"{SYSTEM_PROMPT}\n\n{context}"},
            {"role": "user", "content": question},
        ],
        "temperature": 0.7,
        "max_tokens": 1500,
    }
    data = json.dumps(payload).encode("utf-8")
    
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    
    req = urllib.request.Request(endpoint_url, data=data, headers=headers)
    
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    
    try:
        return result["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        return f"Unexpected response format: {json.dumps(result)[:300]}"


def call_gemini(endpoint_url: str, api_key: str, model: str, question: str, context: str) -> str:
    """Call Google Gemini API (different format from OpenAI)."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": f"{SYSTEM_PROMPT}\n\n{context}\n\n---\nUser question: {question}"}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 1500,
        }
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    
    try:
        return result["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        return f"Unexpected Gemini response: {json.dumps(result)[:300]}"


def call_claude(endpoint_url: str, api_key: str, model: str, question: str, context: str) -> str:
    """Call Anthropic Claude API (different format from OpenAI)."""
    payload = {
        "model": model,
        "max_tokens": 1500,
        "system": f"{SYSTEM_PROMPT}\n\n{context}",
        "messages": [
            {"role": "user", "content": question}
        ]
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(endpoint_url, data=data, headers={
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    })
    
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    
    try:
        return result["content"][0]["text"]
    except (KeyError, IndexError):
        return f"Unexpected Claude response: {json.dumps(result)[:300]}"


# ─── FORMAT ROUTER ────────────────────────────────────────────

FORMAT_MAP = {
    "openai": call_openai_compatible,
    "gemini": call_gemini,
    "claude": call_claude,
}


# ─── MAIN AI CHAT FUNCTION ───────────────────────────────────

def process_ai_question(question: str) -> dict:
    """Process a question using the configured AI provider with live system data."""
    config = load_config()
    api_format = config.get("api_format", "openai")
    endpoint_url = config.get("endpoint_url", "")
    api_key = config.get("api_key", "")
    model = config.get("model_name", "")

    if not endpoint_url:
        return {
            "intent": "no_api",
            "question": question,
            "title": "AI Not Configured",
            "messages": [
                "⚙️ No AI configured. Go to **Settings** to add your LLM API.",
                "Using the built-in rule engine to answer your questions."
            ],
            "actions": [],
            "ai_mode": False,
        }

    call_fn = FORMAT_MAP.get(api_format, call_openai_compatible)

    try:
        # Build fresh system context
        context = build_system_context()
        
        # Call the AI
        response_text = call_fn(endpoint_url, api_key, model, question, context)

        provider_name = config.get("ai_provider", "AI")
        return {
            "intent": "ai_response",
            "question": question,
            "title": f"AI Analysis ({provider_name})",
            "messages": [response_text],
            "actions": [],
            "ai_mode": True,
        }

    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8")[:300]
        except:
            pass
        
        if e.code == 401 or e.code == 403:
            error_msg = "🔴 **Invalid API key.** Please check your key in Settings."
        elif e.code == 429:
            error_msg = "🟡 **Rate limit exceeded.** Wait a moment and try again."
        elif e.code == 404:
            error_msg = f"🔴 **Endpoint not found (404).** Check your API URL in Settings.\nURL: {endpoint_url}"
        else:
            error_msg = f"🔴 **API Error ({e.code}):** {error_body}"

        return {
            "intent": "error",
            "question": question,
            "title": "AI Error",
            "messages": [error_msg],
            "actions": [],
            "ai_mode": False,
        }

    except urllib.error.URLError as e:
        return {
            "intent": "error",
            "question": question,
            "title": "Connection Error",
            "messages": [f"🔴 **Cannot reach API endpoint.**\n\nURL: {endpoint_url}\nError: {str(e.reason)}\n\nCheck your internet connection or if the local server is running."],
            "actions": [],
            "ai_mode": False,
        }

    except Exception as e:
        return {
            "intent": "error",
            "question": question,
            "title": "Error",
            "messages": [f"🔴 **Unexpected error:** {str(e)}"],
            "actions": [],
            "ai_mode": False,
        }
