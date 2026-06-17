"""Deterministic LLM provider for tests, demos, and offline runs.

The real providers (AnthropicProvider) need an API key and a network
round-trip. MockLLMProvider returns scripted responses so pipelines that
call an LLMProvider can be exercised end-to-end with no key, no network,
and a stable output — exactly what examples/tests/CI need.

Response resolution (first match wins):
    - callable(prompt) -> str         : compute the reply from the prompt
    - dict[str, str]                  : substring match on the prompt
    - list[str]                       : FIFO queue, one reply per call
    - str                             : the same reply every call
    - None                            : the `default` reply
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Union

from .base import LLMProvider

Responses = Union[str, List[str], Dict[str, str], Callable[[str], str], None]

_ZERO_USAGE = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


class MockLLMProvider(LLMProvider):
    """An LLMProvider that returns canned text instead of calling a model.

    Records every prompt it receives on `.calls` so tests can assert on
    what was actually sent.
    """

    def __init__(self, responses: Responses = None, *, default: str = "{}"):
        self._responses = responses
        self._default = default
        # A list is consumed FIFO; copy so the caller's list is untouched.
        self._queue: Optional[List[str]] = (
            list(responses) if isinstance(responses, list) else None
        )
        self.calls: List[str] = []

    async def query(self, prompt: str, *, return_usage: bool = False, **kwargs) -> Any:
        self.calls.append(prompt)
        text = self._resolve(prompt)
        return (text, dict(_ZERO_USAGE)) if return_usage else text

    async def query_with_image(
        self,
        prompt: str,
        image_data: Any = None,
        *,
        image_path: Any = None,
        return_usage: bool = False,
        **kwargs,
    ) -> Any:
        # Mock ignores the image (bytes or path); same scripted resolution.
        # image_data=None default + image_path= kwarg mirror AnthropicProvider
        # so callers using either form hit the same contract on the mock.
        return await self.query(prompt, return_usage=return_usage, **kwargs)

    async def query_with_images(
        self, prompt: str, images: Any, *, return_usage: bool = False, **kwargs
    ) -> Any:
        # Mock ignores the images; same scripted resolution as text queries.
        return await self.query(prompt, return_usage=return_usage, **kwargs)

    def _resolve(self, prompt: str) -> str:
        r = self._responses
        if callable(r):
            return r(prompt)
        if isinstance(r, dict):
            for needle, reply in r.items():
                if needle in prompt:
                    return reply
            return self._default
        if self._queue is not None:
            return self._queue.pop(0) if self._queue else self._default
        if isinstance(r, str):
            return r
        return self._default
