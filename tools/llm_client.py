"""
LLMClient — unified Claude / OpenAI / Ollama client.

Provider priority: Claude (claude-opus-4-8) → OpenAI (gpt-4o) → Ollama (llama3)
Features: streaming, retry with exponential backoff, cost tracking
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

PROVIDER_PRIORITY = ["claude", "openai", "ollama"]

DEFAULT_MODELS = {
    "claude": "claude-opus-4-8",
    "openai": "gpt-4o",
    "ollama": "llama3",
}

# USD per 1M tokens (approximate)
COST_TABLE = {
    "claude-opus-4-8":     {"input": 15.0, "output": 75.0},
    "claude-sonnet-4-6":   {"input": 3.0,  "output": 15.0},
    "gpt-4o":              {"input": 2.5,  "output": 10.0},
    "gpt-4o-mini":         {"input": 0.15, "output": 0.6},
    "llama3":              {"input": 0.0,  "output": 0.0},
}

MAX_RETRIES = 3
BASE_DELAY = 1.0


class LLMClient:
    """
    Unified LLM client with automatic provider fallback, streaming, retry, and cost tracking.
    """

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self._providers = self._detect_providers()
        self._memory = None

    def _detect_providers(self) -> list[str]:
        available = []
        if os.getenv("ANTHROPIC_API_KEY"):
            available.append("claude")
        if os.getenv("OPENAI_API_KEY"):
            available.append("openai")
        ollama_url = os.getenv("OLLAMA_BASE_URL", "")
        if ollama_url:
            available.append("ollama")
        if not available:
            logger.warning("No LLM API keys configured. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or OLLAMA_BASE_URL")
        return available

    async def complete(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.3,
        max_tokens: int = 1024,
        provider: str | None = None,
    ) -> str:
        """Complete a prompt. Returns response text. Raises RuntimeError if all providers fail."""
        providers_to_try = [provider] if provider else self._providers
        last_error = None

        for prov in providers_to_try:
            for attempt in range(MAX_RETRIES):
                try:
                    response, usage = await self._call_provider(
                        prov, prompt, system, temperature, max_tokens
                    )
                    self._track_cost(prov, usage)
                    return response
                except Exception as e:
                    last_error = e
                    delay = BASE_DELAY * (2 ** attempt)
                    logger.warning(f"[{prov}] attempt {attempt+1}/{MAX_RETRIES} failed: {e}. Retrying in {delay}s")
                    await asyncio.sleep(delay)

        raise RuntimeError(f"All LLM providers failed. Last error: {last_error}")

    async def stream(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.3,
        max_tokens: int = 1024,
        provider: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream completion tokens. Yields text chunks."""
        providers_to_try = [provider] if provider else self._providers
        for prov in providers_to_try:
            try:
                async for chunk in self._stream_provider(prov, prompt, system, temperature, max_tokens):
                    yield chunk
                return
            except Exception as e:
                logger.warning(f"[{prov}] streaming failed: {e}")

        raise RuntimeError("All LLM providers failed for streaming")

    async def _call_provider(
        self, provider: str, prompt: str, system: str, temperature: float, max_tokens: int
    ) -> tuple[str, dict]:
        if provider == "claude":
            return await self._call_claude(prompt, system, temperature, max_tokens)
        elif provider == "openai":
            return await self._call_openai(prompt, system, temperature, max_tokens)
        elif provider == "ollama":
            return await self._call_ollama(prompt, system, temperature, max_tokens)
        else:
            raise ValueError(f"Unknown provider: {provider}")

    async def _call_claude(self, prompt: str, system: str, temperature: float, max_tokens: int):
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        model = self.config.get("llm", {}).get("claude_model", DEFAULT_MODELS["claude"])
        messages = [{"role": "user", "content": prompt}]
        kwargs = {"model": model, "max_tokens": max_tokens, "temperature": temperature, "messages": messages}
        if system:
            kwargs["system"] = system
        response = await client.messages.create(**kwargs)
        text = response.content[0].text
        usage = {
            "prompt_tokens": response.usage.input_tokens,
            "completion_tokens": response.usage.output_tokens,
            "model": model,
        }
        return text, usage

    async def _call_openai(self, prompt: str, system: str, temperature: float, max_tokens: int):
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
        model = self.config.get("llm", {}).get("openai_model", DEFAULT_MODELS["openai"])
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = await client.chat.completions.create(
            model=model, messages=messages, temperature=temperature, max_tokens=max_tokens
        )
        text = response.choices[0].message.content or ""
        usage = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "model": model,
        }
        return text, usage

    async def _call_ollama(self, prompt: str, system: str, temperature: float, max_tokens: int):
        import aiohttp
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        model = self.config.get("llm", {}).get("ollama_model", DEFAULT_MODELS["ollama"])
        payload = {
            "model": model,
            "prompt": f"{system}\n\n{prompt}" if system else prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url}/api/generate",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                data = await resp.json()
                text = data.get("response", "")
                usage = {"prompt_tokens": 0, "completion_tokens": 0, "model": model}
                return text, usage

    async def _stream_provider(
        self, provider: str, prompt: str, system: str, temperature: float, max_tokens: int
    ) -> AsyncIterator[str]:
        if provider == "claude":
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
            model = DEFAULT_MODELS["claude"]
            messages = [{"role": "user", "content": prompt}]
            kwargs = {"model": model, "max_tokens": max_tokens, "messages": messages}
            if system:
                kwargs["system"] = system
            async with client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    yield text
        elif provider == "openai":
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
            model = DEFAULT_MODELS["openai"]
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            async for chunk in await client.chat.completions.create(
                model=model, messages=messages, temperature=temperature, stream=True
            ):
                content = chunk.choices[0].delta.content
                if content:
                    yield content
        else:
            text, _ = await self._call_ollama(prompt, system, temperature, max_tokens)
            yield text

    def _track_cost(self, provider: str, usage: dict):
        model = usage.get("model", "")
        rates = COST_TABLE.get(model, {"input": 0.0, "output": 0.0})
        cost = (
            usage.get("prompt_tokens", 0) * rates["input"] / 1_000_000
            + usage.get("completion_tokens", 0) * rates["output"] / 1_000_000
        )
        try:
            if self._memory is None:
                from agent.memory.memory_manager import MemoryManager
                self._memory = MemoryManager({})
            self._memory.log_llm_cost(
                provider=provider,
                model=model,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                cost_usd=cost,
                task_type="",
            )
        except Exception:
            pass
        if cost > 0:
            logger.debug(f"LLM cost: ${cost:.6f} ({provider}/{model})")
