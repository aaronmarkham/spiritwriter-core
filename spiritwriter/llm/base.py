"""Abstract LLM provider interface."""

from abc import ABC, abstractmethod
from typing import Optional, Tuple, Any


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
    async def query_with_image(self, prompt: str, image_data: bytes, *, return_usage: bool = False, **kwargs) -> Any:
        """Send a prompt with an image.
        
        Args:
            prompt: The user prompt 
            image_data: Raw image bytes
            return_usage: If True, return (response, usage_dict)
            **kwargs: Provider-specific options
            
        Returns:
            If return_usage=False: response text string  
            If return_usage=True: tuple of (response_text, usage_dict or None)
        """
        ...