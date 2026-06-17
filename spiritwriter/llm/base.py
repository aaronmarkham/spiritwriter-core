"""Abstract LLM provider interface."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Union


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    async def query(self, prompt: str, *, return_usage: bool = False, **kwargs) -> Any:
        """Send a prompt and get a response.

        Args:
            prompt: The user prompt
            return_usage: If True, return (response, usage_dict)
            **kwargs: Provider-specific options

        Returns:
            If return_usage=False: response text string
            If return_usage=True: tuple of (response_text, usage_dict or None)
        """
        ...

    @abstractmethod
    async def query_with_image(
        self,
        prompt: str,
        image_data: Union[bytes, str, Path, None] = None,
        *,
        image_path: Union[str, Path, None] = None,
        return_usage: bool = False,
        **kwargs,
    ) -> Any:
        """Send a prompt with one image.

        Args:
            prompt: The user prompt
            image_data: Raw image bytes, OR a file path (str/Path) to an image.
            image_path: Kwarg-only alias for a file path. If both ``image_data``
                and ``image_path`` are given, ``image_path`` takes precedence.
            return_usage: If True, return (response, usage_dict)
            **kwargs: Provider-specific options (e.g. ``model``)

        Returns:
            If return_usage=False: response text string
            If return_usage=True: tuple of (response_text, usage_dict or None)
        """
        ...

    @abstractmethod
    async def query_with_images(
        self,
        prompt: str,
        images: list,
        *,
        return_usage: bool = False,
        **kwargs,
    ) -> Any:
        """Send a prompt with multiple images.

        Args:
            prompt: The user prompt
            images: Non-empty list; each item may be raw bytes, a file path
                (str/Path), or a pre-formatted
                ``{"data": base64, "media_type": ...}`` dict.
            return_usage: If True, return (response, usage_dict)
            **kwargs: Provider-specific options (e.g. ``model``)

        Returns:
            If return_usage=False: response text string
            If return_usage=True: tuple of (response_text, usage_dict or None)
        """
        ...
