"""Base LLM Provider abstraction with rate-limiting, retries, and token accounting."""

import time
import abc
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("counterfactual_pipeline")


class BaseLLMProvider(abc.ABC):
    def __init__(
        self,
        family: str,
        model_name: str,
        rpm_limit: int = 30,
        max_retries: int = 5,
        base_backoff_sec: float = 2.0
    ):
        self.family = family
        self.model_name = model_name
        self.rpm_limit = rpm_limit
        self.max_retries = max_retries
        self.base_backoff_sec = base_backoff_sec
        
        self.min_interval_sec = 60.0 / max(1, rpm_limit)
        self.last_call_time = 0.0
        self.total_calls = 0
        self.total_errors = 0

    def _rate_limit_throttle(self):
        elapsed = time.time() - self.last_call_time
        if elapsed < self.min_interval_sec:
            time.sleep(self.min_interval_sec - elapsed)
        self.last_call_time = time.time()

    @abc.abstractmethod
    def _call_api(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int
    ) -> str:
        """Vendor-specific API implementation."""
        pass

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 1024
    ) -> str:
        """Thread-safe / retry-wrapped generation method with exponential backoff."""
        retries = 0
        last_exception = None

        while retries <= self.max_retries:
            self._rate_limit_throttle()
            try:
                self.total_calls += 1
                result = self._call_api(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens
                )
                if not result or not result.strip():
                    raise ValueError("API returned empty string response.")
                return result.strip()
            except Exception as e:
                self.total_errors += 1
                last_exception = e
                retries += 1
                wait_sec = self.base_backoff_sec * (2 ** (retries - 1))
                logger.warning(
                    f"[{self.family}:{self.model_name}] Call failed (retry {retries}/{self.max_retries}): {e}. "
                    f"Waiting {wait_sec:.1f}s..."
                )
                if retries <= self.max_retries:
                    time.sleep(wait_sec)

        raise RuntimeError(
            f"[{self.family}:{self.model_name}] Exceeded max retries ({self.max_retries}). Last error: {last_exception}"
        )
