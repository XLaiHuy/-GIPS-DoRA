"""OpenAI-Compatible LLM Provider supporting OpenAI, OpenRouter, DeepSeek, vLLM, Ollama."""

import os
import logging
import httpx
from typing import Optional, Dict, Any
from counterfactual_pipeline.providers.base import BaseLLMProvider

logger = logging.getLogger("counterfactual_pipeline")


class OpenAICompatibleProvider(BaseLLMProvider):
    def __init__(
        self,
        family: str = "openai_compatible",
        model_name: str = "gpt-4o-mini",
        api_key: Optional[str] = None,
        base_url: str = "https://api.openai.com/v1",
        rpm_limit: int = 30,
        timeout_sec: float = 60.0,
        extra_headers: Optional[Dict[str, str]] = None
    ):
        super().__init__(family=family, model_name=model_name, rpm_limit=rpm_limit)
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("OPENROUTER_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.extra_headers = extra_headers or {}

    def _call_api(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int
    ) -> str:
        headers = {
            "Content-Type": "application/json",
            **self.extra_headers
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }

        url = f"{self.base_url}/chat/completions"
        with httpx.Client(timeout=self.timeout_sec) as client:
            resp = client.post(url, headers=headers, json=payload)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"API [{self.family}:{self.model_name}] returned HTTP {resp.status_code}: {resp.text}"
                )
            
            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                raise RuntimeError(f"API returned no choices: {data}")
            
            content = choices[0].get("message", {}).get("content", "")
            return content


class OpenAIProvider(OpenAICompatibleProvider):
    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "gpt-4o-mini",
        rpm_limit: int = 30,
        timeout_sec: float = 60.0
    ):
        super().__init__(
            family="openai",
            model_name=model_name,
            api_key=api_key or os.getenv("OPENAI_API_KEY"),
            base_url="https://api.openai.com/v1",
            rpm_limit=rpm_limit,
            timeout_sec=timeout_sec
        )


class OpenRouterProvider(OpenAICompatibleProvider):
    """Access multiple open-source families (Qwen, LLaMA, DeepSeek, Mistral) via OpenRouter."""
    def __init__(
        self,
        model_name: str = "qwen/qwen-2.5-72b-instruct",
        family: str = "alibaba_qwen",
        api_key: Optional[str] = None,
        rpm_limit: int = 20,
        timeout_sec: float = 60.0
    ):
        key = api_key or os.getenv("OPENROUTER_API_KEY")
        headers = {
            "HTTP-Referer": "https://github.com/gips-dora-vietnamese-nlp",
            "X-Title": "GIPS-DoRA Research Pipeline"
        }
        super().__init__(
            family=family,
            model_name=model_name,
            api_key=key,
            base_url="https://openrouter.ai/api/v1",
            rpm_limit=rpm_limit,
            timeout_sec=timeout_sec,
            extra_headers=headers
        )
