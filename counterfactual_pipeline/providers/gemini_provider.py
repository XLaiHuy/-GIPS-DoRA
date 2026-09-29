"""Google Gemini LLM Provider using direct REST API via httpx."""

import os
import json
import time
import logging
import httpx
from pathlib import Path
from typing import Optional, List
from counterfactual_pipeline.providers.base import BaseLLMProvider

logger = logging.getLogger("counterfactual_pipeline")


def load_env_keys() -> List[str]:
    """Read GEMINI_API_KEYS or GEMINI_API_KEY from os.environ or .env file."""
    keys: List[str] = []
    if os.getenv("GEMINI_API_KEYS"):
        keys.extend([k.strip() for k in os.getenv("GEMINI_API_KEYS").split(",") if k.strip()])
    if os.getenv("GEMINI_API_KEY"):
        k = os.getenv("GEMINI_API_KEY").strip()
        if k and k not in keys:
            keys.append(k)
            
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEYS="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                for k in val.split(","):
                    k = k.strip()
                    if k and k not in keys:
                        keys.append(k)
            elif line.startswith("GEMINI_API_KEY="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val and val not in keys:
                    keys.append(val)
    return keys


def load_env_key() -> str:
    """Read first available GEMINI_API_KEY."""
    keys = load_env_keys()
    return keys[0] if keys else ""


class GeminiProvider(BaseLLMProvider):
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_keys: Optional[List[str]] = None,
        model_name: str = "gemini-3.8-flash",
        rpm_limit: int = 12,  # ~5.0s per call safe delay
        timeout_sec: float = 60.0,
        fallback_models: Optional[List[str]] = None
    ):
        super().__init__(family="gemini", model_name=model_name, rpm_limit=rpm_limit)
        loaded = load_env_keys()
        self.api_keys: List[str] = []
        if api_keys:
            self.api_keys.extend(api_keys)
        elif api_key:
            self.api_keys.append(api_key)
        elif loaded:
            self.api_keys.extend(loaded)
            
        if not self.api_keys:
            logger.warning("GeminiProvider initialized without API keys.")
        self.api_key = self.api_keys[0] if self.api_keys else ""
        self.timeout_sec = timeout_sec
        
        # Primary model and fallback chain across all active Google AI Studio models
        self.models_to_try = [model_name]
        default_chain = [
            "gemini-3.6-flash",
            "gemini-3.5-flash",
            "gemini-3.1-flash-lite",
            "gemini-3.5-flash-lite",
            "gemini-3.7-flash"
        ]
        if fallback_models:
            self.models_to_try.extend([m for m in fallback_models if m != model_name])
        else:
            for fb in default_chain:
                if fb not in self.models_to_try:
                    self.models_to_try.append(fb)
        self.exhausted_until = {}  # (key, model_name) -> timestamp until which to skip

    def _call_api(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int
    ) -> str:
        if not self.api_keys:
            raise ValueError("No GEMINI_API_KEY configured.")

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_prompt}]
                }
            ],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens
            }
        }
        
        if system_prompt:
            payload["systemInstruction"] = {
                "parts": [{"text": system_prompt}]
            }

        headers = {"Content-Type": "application/json"}
        last_error = None
        now = time.time()

        with httpx.Client(timeout=self.timeout_sec) as client:
            for model in self.models_to_try:
                for key in self.api_keys:
                    key_tag = key[:14] + "..."
                    # Skip if this (key, model) is in cooldown
                    if now < self.exhausted_until.get((key, model), 0):
                        remaining = int(self.exhausted_until[(key, model)] - now)
                        last_error = f"Model {model} (key {key_tag}) in 429 quota cooldown for {remaining}s"
                        continue

                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                    try:
                        resp = client.post(url, headers=headers, json=payload)
                        if resp.status_code == 200:
                            data = resp.json()
                            candidates = data.get("candidates", [])
                            if candidates:
                                parts = candidates[0].get("content", {}).get("parts", [])
                                if parts:
                                    return parts[0].get("text", "").strip()
                        elif resp.status_code in (429, 503):
                            if resp.status_code == 429:
                                # Cache 429 daily exhaustion for this (key, model) pair
                                self.exhausted_until[(key, model)] = time.time() + 900
                            logger.warning(
                                f"Model {model} with key {key_tag} returned HTTP {resp.status_code}. "
                                f"Falling back to next key/model..."
                            )
                            last_error = f"HTTP {resp.status_code}: {resp.text}"
                            continue
                        else:
                            raise RuntimeError(f"Gemini API returned error HTTP {resp.status_code}: {resp.text}")
                    except httpx.RequestError as e:
                        last_error = str(e)
                        continue

        raise RuntimeError(f"All model candidates {self.models_to_try} across {len(self.api_keys)} keys failed. Last error: {last_error}")
