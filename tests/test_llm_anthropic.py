"""Tests for spiritwriter.llm.anthropic — model configuration."""

from unittest.mock import MagicMock, patch

import pytest

from spiritwriter.llm import AnthropicProvider, DEFAULT_ANTHROPIC_MODEL


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


class TestModelUsedInQuery:
    """Verify the configured model is actually passed to the Anthropic SDK."""

    @pytest.mark.asyncio
    async def test_query_uses_configured_model(self):
        provider = AnthropicProvider(model="claude-test-model-123")

        fake_response = MagicMock()
        fake_response.content = [MagicMock(text="ok")]
        fake_response.usage = MagicMock(input_tokens=1, output_tokens=1)

        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_response

        fake_anthropic = MagicMock()
        fake_anthropic.Anthropic.return_value = fake_client

        with patch.dict("sys.modules", {"claude_agent_sdk": None, "anthropic": fake_anthropic}), \
             patch("spiritwriter.llm.anthropic.get_api_key", return_value="fake-key"):
            await provider.query("hello")

        create_kwargs = fake_client.messages.create.call_args.kwargs
        assert create_kwargs["model"] == "claude-test-model-123"

    @pytest.mark.asyncio
    async def test_query_per_call_model_override(self):
        provider = AnthropicProvider(model="constructor-model")

        fake_response = MagicMock()
        fake_response.content = [MagicMock(text="ok")]
        fake_response.usage = MagicMock(input_tokens=1, output_tokens=1)

        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_response

        fake_anthropic = MagicMock()
        fake_anthropic.Anthropic.return_value = fake_client

        with patch.dict("sys.modules", {"claude_agent_sdk": None, "anthropic": fake_anthropic}), \
             patch("spiritwriter.llm.anthropic.get_api_key", return_value="fake-key"):
            await provider.query("hello", model="per-call-model")

        create_kwargs = fake_client.messages.create.call_args.kwargs
        assert create_kwargs["model"] == "per-call-model"
