"""
Anthropic Claude Provider - Clean abstraction over the SDK
Handles all the message parsing and API interaction
"""

import re
import json
import base64
from pathlib import Path
from typing import Optional, Dict, Any, Union
from spiritwriter.secrets import get_api_key
from .base import LLMProvider


DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"


_SUFFIX_MEDIA_TYPE = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _media_type_from_bytes(data: bytes) -> str:
    """Sniff an image's media type from its magic bytes (defaults to jpeg)."""
    if data.startswith(b"\xFF\xD8"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"GIF"):
        return "image/gif"
    if data.startswith(b"RIFF") and b"WEBP" in data[:20]:
        return "image/webp"
    return "image/jpeg"


def _image_source_block(image: Union[bytes, bytearray, str, Path, dict]) -> Dict[str, Any]:
    """Build an Anthropic image content block from raw bytes, a file path
    (str/Path), or a pre-formatted ``{"data": base64, "media_type": ...}`` dict."""
    if isinstance(image, dict):
        if "data" not in image:
            raise ValueError("pre-formatted image dict must have a 'data' key")
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": image.get("media_type", "image/jpeg"),
                "data": image["data"],
            },
        }
    if isinstance(image, (str, Path)):
        path = Path(image)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {path}")
        raw = path.read_bytes()
        media_type = _SUFFIX_MEDIA_TYPE.get(path.suffix.lower()) or _media_type_from_bytes(raw)
    elif isinstance(image, (bytes, bytearray)):
        raw = bytes(image)
        media_type = _media_type_from_bytes(raw)
    else:
        raise ValueError(f"unsupported image input: {type(image).__name__}")
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(raw).decode("utf-8"),
        },
    }


class AnthropicProvider(LLMProvider):
    """
    Anthropic Claude LLM provider that implements the LLMProvider interface
    Handles all the message object parsing internally
    """

    def __init__(self, debug: bool = False, model: Optional[str] = None):
        self.debug = debug
        self.model = model or DEFAULT_ANTHROPIC_MODEL
        
    async def query(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        return_usage: bool = False,
        **kwargs
    ) -> Union[str, tuple[str, Optional[Dict[str, int]]]]:
        """
        Send a query to Claude and get back clean text response with optional usage metadata

        Args:
            prompt: The user prompt
            system_prompt: Optional system prompt (not supported in simple query API)
            return_usage: If True, return (response, usage_dict); if False, return just response
            **kwargs: Additional provider-specific options. Recognized:
                - model (str): Override the instance's default model for this call.

        Returns:
            If return_usage=False (default): Just the response text string
            If return_usage=True: Tuple of (response_text, usage_dict) where usage_dict contains:
                - input_tokens: Number of input tokens
                - output_tokens: Number of output tokens
                - total_tokens: Total tokens used
                Returns (response_text, None) if usage data unavailable

        Raises:
            ImportError: If neither Claude Agent SDK nor Anthropic SDK is available
        """
        # If system prompt provided, prepend it
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n{prompt}"
        else:
            full_prompt = prompt

        selected_model = kwargs.get("model") or self.model
        response_text = ""
        usage = None

        if self.debug:
            print(f"\n[DEBUG] Sending prompt ({len(full_prompt)} chars) with model={selected_model}")

        # Try Claude Agent SDK first
        try:
            from claude_agent_sdk import query, ClaudeAgentOptions
            options = ClaudeAgentOptions(model=selected_model)
            async for message in query(prompt=full_prompt, options=options):
                text = self._extract_text_from_message(message)
                if text:
                    response_text += text
                # Try to extract usage from message if available
                if hasattr(message, 'usage') and message.usage:
                    usage = {
                        'input_tokens': getattr(message.usage, 'input_tokens', 0),
                        'output_tokens': getattr(message.usage, 'output_tokens', 0),
                    }
                    usage['total_tokens'] = usage['input_tokens'] + usage['output_tokens']
        except (ImportError, Exception) as sdk_err:
            # Fall back to Anthropic SDK (catches both missing SDK and runtime errors)
            if self.debug and not isinstance(sdk_err, ImportError):
                print(f"[DEBUG] Claude Agent SDK failed: {sdk_err}, falling back to Anthropic SDK")
            try:
                import anthropic

                api_key = get_api_key("ANTHROPIC_API_KEY")
                if not api_key:
                    raise ValueError(
                        "ANTHROPIC_API_KEY not set (check keychain or env). "
                        "For testing without API keys, use MockLLMProvider"
                    )

                client = anthropic.Anthropic(api_key=api_key)
                response = client.messages.create(
                    model=selected_model,
                    max_tokens=16384,
                    messages=[{"role": "user", "content": full_prompt}]
                )
                response_text = response.content[0].text

                # Extract usage from Anthropic SDK response
                if hasattr(response, 'usage'):
                    usage = {
                        'input_tokens': response.usage.input_tokens,
                        'output_tokens': response.usage.output_tokens,
                        'total_tokens': response.usage.input_tokens + response.usage.output_tokens,
                    }
            except ImportError:
                raise ImportError(
                    "Neither claude-agent-sdk nor anthropic SDK is installed. "
                    "Install one with: pip install anthropic\n"
                    "For testing without API keys, use MockLLMProvider"
                )

        if self.debug:
            print(f"[DEBUG] Received response ({len(response_text)} chars)")
            if usage:
                print(f"[DEBUG] Usage: {usage['input_tokens']} in + {usage['output_tokens']} out = {usage['total_tokens']} total")
            print("[DEBUG] First 500 chars:")
            print(response_text[:500])
            print("[DEBUG] ---")

        # Return based on return_usage parameter (backward compatibility)
        if return_usage:
            return response_text.strip(), usage
        else:
            return response_text.strip()

    async def query_with_image(
        self,
        prompt: str,
        image_data: Union[bytes, str, Path, None] = None,
        system_prompt: Optional[str] = None,
        return_usage: bool = False,
        *,
        image_path: Union[str, Path, None] = None,
        **kwargs
    ) -> Union[str, tuple[str, Optional[Dict[str, int]]]]:
        """Send a query to Claude with one image for vision analysis.

        Args:
            prompt: The user prompt.
            image_data: Raw image bytes, OR a file path (str/Path) to an image.
            system_prompt: Optional system prompt.
            return_usage: If True, return (response, usage_dict).
            image_path: Kwarg-only alias for passing a file path. If both
                ``image_data`` and ``image_path`` are given, ``image_path``
                takes precedence.
            **kwargs: Recognized: ``model`` (str) — override the instance model.

        Returns:
            Response text, or (text, usage_dict) when ``return_usage`` is True.

        Raises:
            FileNotFoundError: If a path is given and the file does not exist.
            ValueError: If no image is supplied or the input type is unsupported.
            ImportError: If the Anthropic SDK is not available.
        """
        source = image_path if image_path is not None else image_data
        if source is None:
            raise ValueError("query_with_image requires image bytes or a path")
        return await self._vision_query(
            prompt, [source], system_prompt, return_usage, kwargs.get("model")
        )

    async def query_with_images(
        self,
        prompt: str,
        images: list[Union[bytes, bytearray, str, Path, dict]],
        system_prompt: Optional[str] = None,
        return_usage: bool = False,
        **kwargs
    ) -> Union[str, tuple[str, Optional[Dict[str, int]]]]:
        """Send a query to Claude with multiple images for vision analysis.

        Each item in ``images`` may be raw bytes, a file path (str/Path), or a
        pre-formatted ``{"data": base64, "media_type": ...}`` dict.
        """
        return await self._vision_query(
            prompt, list(images), system_prompt, return_usage, kwargs.get("model")
        )

    async def _vision_query(
        self,
        prompt: str,
        images: list[Union[bytes, bytearray, str, Path, dict]],
        system_prompt: Optional[str],
        return_usage: bool,
        model: Optional[str],
    ) -> Union[str, tuple[str, Optional[Dict[str, int]]]]:
        """Shared vision path: build image blocks, then call the Anthropic SDK.

        Image blocks are built before the SDK import, so a missing-file path
        raises FileNotFoundError regardless of whether the SDK is installed.
        """
        if not images:
            raise ValueError("at least one image is required")
        message_content = [_image_source_block(img) for img in images]
        message_content.append({"type": "text", "text": prompt})

        if self.debug:
            print(f"\n[DEBUG] Sending vision query with {len(images)} image(s)")

        try:
            import anthropic

            api_key = get_api_key("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError(
                    "ANTHROPIC_API_KEY not set (check keychain or env). "
                    "Vision queries require the Anthropic SDK."
                )

            client = anthropic.Anthropic(api_key=api_key)
            create_kwargs = {
                "model": model or self.model,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": message_content}],
            }
            if system_prompt:
                create_kwargs["system"] = system_prompt

            response = client.messages.create(**create_kwargs)
            response_text = response.content[0].text.strip()

            usage = None
            if hasattr(response, "usage"):
                usage = {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
                }

            if self.debug:
                print(f"[DEBUG] Received vision response ({len(response_text)} chars)")

            return (response_text, usage) if return_usage else response_text

        except ImportError:
            raise ImportError(
                "anthropic SDK is required for vision queries. "
                "Install with: pip install anthropic"
            )

    def _extract_text_from_message(self, message) -> str:
        """Extract text content from Claude SDK message objects"""
        
        # Try to access message attributes directly first (most reliable)
        # This avoids string parsing issues
        
        # Check for content attribute (list of content blocks)
        if hasattr(message, 'content') and message.content:
            if isinstance(message.content, list):
                texts = []
                for item in message.content:
                    if hasattr(item, 'text'):
                        texts.append(item.text)
                if texts:
                    return '\n'.join(texts)
            elif isinstance(message.content, str):
                return message.content
        
        # Check for result attribute (final result)
        if hasattr(message, 'result'):
            result = message.result
            if isinstance(result, str):
                return result
        
        # Fallback to string parsing (less reliable due to escaping issues)
        msg_str = str(message)
        
        # Skip system initialization messages
        if 'SystemMessage' in msg_str and 'subtype' in msg_str:
            return ""
        
        # Look for ResultMessage - but be careful with escaping
        # We need to extract the actual result value, not the escaped string repr
        if hasattr(message, '__dict__'):
            if 'result' in message.__dict__:
                return str(message.__dict__['result'])
        
        return ""


def _repair_truncated_json(json_str: str) -> str:
    """
    Attempt to repair truncated JSON by closing unclosed brackets/braces.

    This handles cases where LLM output is cut off mid-response.
    """
    # Count unclosed brackets
    open_braces = json_str.count('{') - json_str.count('}')
    open_brackets = json_str.count('[') - json_str.count(']')

    if open_braces <= 0 and open_brackets <= 0:
        return json_str  # Already balanced or over-closed

    # Try to find the last complete value and truncate there
    # Look for patterns like: "key": value, or "key": "value",
    # and try to close after them

    repaired = json_str.rstrip()

    # Remove trailing comma if present
    if repaired.endswith(','):
        repaired = repaired[:-1]

    # If we're in the middle of a string, try to close it
    # Count quotes to see if we're mid-string
    quote_count = repaired.count('"') - repaired.count('\\"')
    if quote_count % 2 == 1:
        repaired += '"'

    # Close any unclosed brackets/braces
    # We need to close in reverse order of what's open
    # Simple heuristic: close brackets first (arrays), then braces (objects)
    stack = []
    for char in json_str:
        if char == '{':
            stack.append('}')
        elif char == '[':
            stack.append(']')
        elif char in '}]':
            if stack and stack[-1] == char:
                stack.pop()

    # Close in reverse order
    repaired += ''.join(reversed(stack))

    return repaired


class JSONExtractor:
    """Utility to extract JSON from Claude responses"""

    @staticmethod
    def extract(response: str, debug: bool = False) -> Dict[str, Any]:
        """
        Extract JSON from Claude response, handling various formats
        
        Args:
            response: Raw text response from Claude
            debug: Print debug info if True
            
        Returns:
            Parsed JSON dict
            
        Raises:
            ValueError: If no valid JSON found
        """
        if not response or not response.strip():
            raise ValueError("Empty response")
        
        response = response.strip()
        
        if debug:
            print(f"\n[JSON EXTRACT DEBUG] Response length: {len(response)}")
            print(f"[JSON EXTRACT DEBUG] First 300 chars:\n{response[:300]}")
        
        # Try markdown code blocks first (most common)
        json_match = re.search(
            r'```(?:json)?\s*\n(.*?)\n```',
            response,
            re.DOTALL | re.IGNORECASE
        )
        if json_match:
            json_str = json_match.group(1).strip()
            if debug:
                print(f"[JSON EXTRACT DEBUG] Found code block, length: {len(json_str)}")
            try:
                return json.loads(json_str)
            except json.JSONDecodeError as e:
                if debug:
                    print(f"[JSON EXTRACT DEBUG] Code block parse failed: {e}")
                    print(f"[JSON EXTRACT DEBUG] Problematic JSON:\n{json_str}")
                # Try to fix common issues
                # Remove any backslash escapes that aren't valid JSON escapes
                json_str_fixed = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', json_str)
                try:
                    return json.loads(json_str_fixed)
                except json.JSONDecodeError:
                    pass

        # Try markdown code blocks without closing fence (truncated response)
        # This handles cases like: ```json\n{...} without the closing ```
        open_fence_match = re.search(
            r'```(?:json)?\s*\n(\{.*)',
            response,
            re.DOTALL | re.IGNORECASE
        )
        if open_fence_match:
            json_str = open_fence_match.group(1).strip()
            # Remove trailing ``` if present (partial fence)
            json_str = re.sub(r'```\s*$', '', json_str).strip()
            if debug:
                print(f"[JSON EXTRACT DEBUG] Found open code block, length: {len(json_str)}")
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                # Try to repair truncated JSON
                repaired = _repair_truncated_json(json_str)
                if debug:
                    print(f"[JSON EXTRACT DEBUG] Attempting repair, added: {len(repaired) - len(json_str)} chars")
                try:
                    return json.loads(repaired)
                except json.JSONDecodeError as e:
                    if debug:
                        print(f"[JSON EXTRACT DEBUG] Repair failed: {e}")
                    pass

        # Try to find JSON object anywhere in the text
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass
        
        # Try parsing the whole response as JSON
        try:
            return json.loads(response)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"No valid JSON found in response.\n"
                f"Error: {e}\n"
                f"Response preview: {response[:300]}"
            )


# Backward compatibility aliases
ClaudeClient = AnthropicProvider