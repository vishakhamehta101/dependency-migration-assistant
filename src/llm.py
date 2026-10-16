"""
Dependency Migration Assistant — the AI model connection (Ollama)

OllamaModel is the `llm_call(system, user, schema) -> str` that explain.py expects. It talks to a
local Ollama server over HTTP: the JSON schema goes in the `format` field so the reply must
match our answer shape, temperature is 0 (plus a fixed seed) so runs repeat, and every call
records its timings for the cost report. Any failure (server down, model missing, timeout, a
reply with no message) raises an exception, which explain.py turns into a "model_error" finding.

Which model to use comes from configs/llm.json, not from the code.
"""

import json
import logging
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "configs/llm.json"
DEFAULT_URL = "http://localhost:11434"


def load_llm_config(path: str = DEFAULT_CONFIG_PATH) -> dict | None:
    """Read the AI model settings. Returns None, after logging why, if they cannot be read."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.error("AI settings file not found: %s", path)
    except json.JSONDecodeError as error:
        logger.error("Invalid JSON in %s: %s", path, error)
    return None


def is_server_up(base_url: str = DEFAULT_URL, timeout: float = 3.0) -> bool:
    """True if an Ollama server answers at this address."""
    try:
        return requests.get(base_url, timeout=timeout).ok
    except requests.RequestException:
        return False


def list_models(base_url: str = DEFAULT_URL, timeout: float = 5.0) -> list:
    """Names of the models the server has downloaded ([] if it cannot be reached)."""
    try:
        response = requests.get(f"{base_url}/api/tags", timeout=timeout)
        response.raise_for_status()
        return [model["name"] for model in response.json().get("models", [])]
    except (requests.RequestException, ValueError, KeyError):
        return []


class OllamaModel:
    """A callable `llm_call(system, user, schema) -> str` backed by a local Ollama server."""

    def __init__(self, model: str, base_url: str = DEFAULT_URL, temperature: float = 0, num_ctx: int = 4096,
                 timeout: float = 300, seed: int = 0):
        self.model, self.base_url = model, base_url.rstrip("/")
        self.temperature, self.num_ctx, self.timeout, self.seed = temperature, num_ctx, timeout, seed
        self.stats = []  # one entry per call: seconds, load_seconds, prompt_tokens, output_tokens, tokens_per_second

    @classmethod
    def from_config(cls, config: dict) -> "OllamaModel":
        return cls(model=config["model"], base_url=config.get("base_url", DEFAULT_URL),
                   temperature=config.get("temperature", 0), num_ctx=config.get("num_ctx", 4096),
                   timeout=config.get("timeout_seconds", 300), seed=config.get("seed", 0))

    def __call__(self, system: str, user: str, schema: dict) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "format": schema,
            "stream": False,
            "options": {"temperature": self.temperature, "num_ctx": self.num_ctx, "seed": self.seed},
        }
        started = time.perf_counter()
        response = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
        if not response.ok:
            try:
                detail = response.json().get("error", response.text)
            except ValueError:
                detail = response.text
            raise RuntimeError(f"Ollama returned HTTP {response.status_code}: {detail}")

        data = response.json()
        message = data.get("message") or {}
        if "content" not in message:
            raise RuntimeError("Ollama's reply contained no message")

        output_tokens = data.get("eval_count", 0)
        output_seconds = (data.get("eval_duration") or 0) / 1e9
        self.stats.append({
            "seconds": time.perf_counter() - started,
            "load_seconds": (data.get("load_duration") or 0) / 1e9,
            "prompt_tokens": data.get("prompt_eval_count", 0),
            "output_tokens": output_tokens,
            "tokens_per_second": output_tokens / output_seconds if output_seconds else 0.0,
        })
        return message["content"]
