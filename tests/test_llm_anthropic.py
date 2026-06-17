"""Tests for spiritwriter.llm.anthropic — model configuration."""

from unittest.mock import MagicMock, patch

import pytest

from spiritwriter.llm import AnthropicProvider, DEFAULT_ANTHROPIC_MODEL
from spiritwriter.llm.anthropic import JSONExtractor


class TestModelConfiguration:
    def test_default_model(self):
        provider = AnthropicProvider()
        assert provider.model == DEFAULT_ANTHROPIC_MODEL

    def test_custom_model_via_constructor(self):
        provider = AnthropicProvider(model="claude-opus-4-5-20250101")
        assert provider.model == "claude-opus-4-5-20250101"

    def test_none_model_falls_back_to_default(self):
        provider = AnthropicProvider(model=None)
        assert provider.model == DEFAULT_ANTHROPIC_MODEL

    def test_default_model_constant_exported(self):
        assert isinstance(DEFAULT_ANTHROPIC_MODEL, str)
        assert DEFAULT_ANTHROPIC_MODEL.startswith("claude-")


def _make_fake_anthropic(text: str = "ok"):
    """Build a MagicMock that mimics the `anthropic` module surface we use."""
    fake_response = MagicMock()
    fake_response.content = [MagicMock(text=text)]
    fake_response.usage = MagicMock(input_tokens=1, output_tokens=1)

    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    fake_anthropic = MagicMock()
    fake_anthropic.Anthropic.return_value = fake_client
    return fake_anthropic, fake_client


class TestQueryModelThreading:
    """Verify the configured model reaches the Anthropic SDK fallback path."""

    @pytest.mark.asyncio
    async def test_query_uses_configured_model(self):
        provider = AnthropicProvider(model="claude-test-model-123")
        fake_anthropic, fake_client = _make_fake_anthropic()

        with patch.dict("sys.modules", {"claude_agent_sdk": None, "anthropic": fake_anthropic}), \
             patch("spiritwriter.llm.anthropic.get_api_key", return_value="fake-key"):
            await provider.query("hello")

        assert fake_client.messages.create.call_args.kwargs["model"] == "claude-test-model-123"

    @pytest.mark.asyncio
    async def test_query_per_call_model_override(self):
        provider = AnthropicProvider(model="constructor-model")
        fake_anthropic, fake_client = _make_fake_anthropic()

        with patch.dict("sys.modules", {"claude_agent_sdk": None, "anthropic": fake_anthropic}), \
             patch("spiritwriter.llm.anthropic.get_api_key", return_value="fake-key"):
            await provider.query("hello", model="per-call-model")

        assert fake_client.messages.create.call_args.kwargs["model"] == "per-call-model"

    @pytest.mark.asyncio
    async def test_query_per_call_model_none_falls_back_to_instance(self):
        """model=None in a call must not override the instance default."""
        provider = AnthropicProvider(model="constructor-model")
        fake_anthropic, fake_client = _make_fake_anthropic()

        with patch.dict("sys.modules", {"claude_agent_sdk": None, "anthropic": fake_anthropic}), \
             patch("spiritwriter.llm.anthropic.get_api_key", return_value="fake-key"):
            await provider.query("hello", model=None)

        assert fake_client.messages.create.call_args.kwargs["model"] == "constructor-model"


class TestQueryWithImageModelThreading:
    @pytest.mark.asyncio
    async def test_query_with_image_uses_configured_model(self):
        provider = AnthropicProvider(model="vision-model-xyz")
        fake_anthropic, fake_client = _make_fake_anthropic()
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

        with patch.dict("sys.modules", {"anthropic": fake_anthropic}), \
             patch("spiritwriter.llm.anthropic.get_api_key", return_value="fake-key"):
            await provider.query_with_image("describe", png)

        assert fake_client.messages.create.call_args.kwargs["model"] == "vision-model-xyz"

    @pytest.mark.asyncio
    async def test_query_with_image_per_call_override(self):
        provider = AnthropicProvider(model="ctor-model")
        fake_anthropic, fake_client = _make_fake_anthropic()
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

        with patch.dict("sys.modules", {"anthropic": fake_anthropic}), \
             patch("spiritwriter.llm.anthropic.get_api_key", return_value="fake-key"):
            await provider.query_with_image("describe", png, model="call-model")

        assert fake_client.messages.create.call_args.kwargs["model"] == "call-model"

    @pytest.mark.asyncio
    async def test_query_with_image_per_call_none_falls_back(self):
        provider = AnthropicProvider(model="ctor-model")
        fake_anthropic, fake_client = _make_fake_anthropic()
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

        with patch.dict("sys.modules", {"anthropic": fake_anthropic}), \
             patch("spiritwriter.llm.anthropic.get_api_key", return_value="fake-key"):
            await provider.query_with_image("describe", png, model=None)

        assert fake_client.messages.create.call_args.kwargs["model"] == "ctor-model"


class TestAgentSDKPathModelThreading:
    """The Agent SDK path is the default — make sure model actually reaches it."""

    def _make_fake_agent_sdk(self):
        """Build a fake claude_agent_sdk module.

        query() returns an empty async iterator so the provider finishes without
        error. ClaudeAgentOptions is a MagicMock we can introspect afterward.
        """
        fake_options_cls = MagicMock(name="ClaudeAgentOptions")

        async def fake_query(prompt, options=None):
            # Stash the options on the query mock so the test can inspect it.
            fake_query.last_options = options
            fake_query.last_prompt = prompt
            if False:  # pragma: no cover — make this an async generator
                yield None

        fake_query.last_options = None
        fake_query.last_prompt = None

        fake_sdk = MagicMock()
        fake_sdk.query = fake_query
        fake_sdk.ClaudeAgentOptions = fake_options_cls
        return fake_sdk, fake_options_cls, fake_query

    @pytest.mark.asyncio
    async def test_agent_sdk_receives_configured_model(self):
        provider = AnthropicProvider(model="agent-sdk-model")
        fake_sdk, fake_options_cls, fake_query = self._make_fake_agent_sdk()

        with patch.dict("sys.modules", {"claude_agent_sdk": fake_sdk}):
            await provider.query("hello")

        # ClaudeAgentOptions was instantiated with the configured model.
        fake_options_cls.assert_called_once_with(model="agent-sdk-model")
        # And the options object was forwarded to query().
        assert fake_query.last_options is fake_options_cls.return_value

    @pytest.mark.asyncio
    async def test_agent_sdk_honors_per_call_override(self):
        provider = AnthropicProvider(model="ctor-model")
        fake_sdk, fake_options_cls, fake_query = self._make_fake_agent_sdk()

        with patch.dict("sys.modules", {"claude_agent_sdk": fake_sdk}):
            await provider.query("hello", model="override-model")

        fake_options_cls.assert_called_once_with(model="override-model")


class TestJSONExtractor:
    """JSONExtractor.extract — the LLM-JSON parser shared with CSP.

    This is the guarding test neither repo had before the consolidation
    (spiritwriter-core#76 / claude-studio-producer#15, port step 2).
    """

    def test_fenced_json_block(self):
        assert JSONExtractor.extract('```json\n{"a": 1}\n```') == {"a": 1}

    def test_fenced_block_without_language(self):
        assert JSONExtractor.extract('```\n{"a": 2}\n```') == {"a": 2}

    def test_bare_object(self):
        assert JSONExtractor.extract('{"a": 3}') == {"a": 3}

    def test_object_embedded_in_prose(self):
        assert JSONExtractor.extract('Here is the result: {"a": 4} — done.') == {"a": 4}

    def test_truncated_fenced_json_is_repaired(self):
        # Unclosed array + missing closing brace/fence (LLM output cut off).
        out = JSONExtractor.extract('```json\n{"a": 5, "b": [1, 2')
        assert out == {"a": 5, "b": [1, 2]}

    def test_invalid_backslash_escape_is_fixed(self):
        # A lone backslash (e.g. a Windows path) isn't valid JSON; the
        # extractor's escape-fix pass should still parse it.
        out = JSONExtractor.extract(r'```json' + '\n' + r'{"path": "C:\Users\x"}' + '\n```')
        assert out["path"].startswith("C:")

    def test_empty_response_raises(self):
        with pytest.raises(ValueError):
            JSONExtractor.extract("")

    def test_no_json_raises(self):
        with pytest.raises(ValueError):
            JSONExtractor.extract("just some prose, no JSON here")
