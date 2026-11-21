"""Tests for spiritwriter.llm.anthropic — model configuration."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from spiritwriter.llm import AnthropicProvider, DEFAULT_ANTHROPIC_MODEL, MockLLMProvider
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


class TestAgentSDKPathReplyText:
    """The Agent SDK stream carries the reply twice: in the AssistantMessage's
    text blocks and again in the final ResultMessage's ``.result``. The
    provider must return it once."""

    @staticmethod
    def _make_fake_agent_sdk(messages):
        async def fake_query(prompt, options=None):
            for message in messages:
                yield message

        fake_sdk = MagicMock()
        fake_sdk.query = fake_query
        return fake_sdk

    # The fakes mirror the field shapes of the real claude_agent_sdk dataclasses
    # (AssistantMessage, UserMessage, ResultMessage): usage is a dict, result
    # may be None, and tool-use blocks carry no `text`.

    @staticmethod
    def _assistant(*texts, parent_tool_use_id=None, tool_use=False):
        content = [SimpleNamespace(text=t) for t in texts]
        if tool_use:
            content.append(SimpleNamespace(id="toolu_1", name="Read", input={}))
        return SimpleNamespace(
            content=content, model="m", parent_tool_use_id=parent_tool_use_id, error=None
        )

    @staticmethod
    def _user(content):
        return SimpleNamespace(content=content, uuid="u1", parent_tool_use_id=None)

    @staticmethod
    def _result(text, *, is_error=False, subtype="success", usage=None):
        return SimpleNamespace(
            subtype=subtype, duration_ms=1, duration_api_ms=1, is_error=is_error,
            num_turns=1, session_id="s", total_cost_usd=0.0, usage=usage,
            result=text, structured_output=None,
        )

    async def _run(self, messages, **kwargs):
        fake_sdk = self._make_fake_agent_sdk(messages)
        with patch.dict("sys.modules", {"claude_agent_sdk": fake_sdk}):
            return await AnthropicProvider(model="m").query("hello", **kwargs)

    @pytest.mark.asyncio
    async def test_reply_is_not_duplicated_by_result_message(self):
        reply = '{"word": "ruler"}'
        fake_sdk = self._make_fake_agent_sdk([
            SimpleNamespace(subtype="init", data={}),
            self._assistant(reply),
            self._result(reply),
        ])

        with patch.dict("sys.modules", {"claude_agent_sdk": fake_sdk}):
            out = await AnthropicProvider(model="m").query("hello")

        assert out == reply
        assert JSONExtractor.extract(out) == {"word": "ruler"}

    @pytest.mark.asyncio
    async def test_result_message_used_when_no_assistant_text(self):
        fake_sdk = self._make_fake_agent_sdk([self._result("only-result")])

        with patch.dict("sys.modules", {"claude_agent_sdk": fake_sdk}):
            out = await AnthropicProvider(model="m").query("hello")

        assert out == "only-result"

    @pytest.mark.asyncio
    async def test_usage_dict_is_read(self):
        out, usage = await self._run(
            [self._assistant("hi"),
             self._result("hi", usage={"input_tokens": 5, "output_tokens": 7,
                                       "cache_read_input_tokens": 3})],
            return_usage=True,
        )
        assert out == "hi"
        assert usage == {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12}

    @pytest.mark.asyncio
    async def test_none_result_does_not_leak_into_reply(self):
        out = await self._run([self._assistant("the reply"), self._result(None)])
        assert out == "the reply"

    @pytest.mark.asyncio
    async def test_error_result_falls_back_instead_of_returning_error_text(self):
        """is_error=True: neither the error text nor partial output is the reply."""
        fake_sdk = self._make_fake_agent_sdk([
            self._assistant("partial outp"),
            self._result("API Error: 529 overloaded", is_error=True),
        ])
        fake_anthropic, fake_client = _make_fake_anthropic(text="fallback reply")

        with patch.dict("sys.modules", {"claude_agent_sdk": fake_sdk, "anthropic": fake_anthropic}), \
             patch("spiritwriter.llm.anthropic.get_api_key", return_value="fake-key"):
            out = await AnthropicProvider(model="m").query("hello")

        assert out == "fallback reply"
        fake_client.messages.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_result_without_fallback_key_raises(self):
        fake_sdk = self._make_fake_agent_sdk([
            self._result("max turns reached", is_error=True, subtype="error_max_turns"),
        ])
        fake_anthropic, _ = _make_fake_anthropic()

        with patch.dict("sys.modules", {"claude_agent_sdk": fake_sdk, "anthropic": fake_anthropic}), \
             patch("spiritwriter.llm.anthropic.get_api_key", return_value=None):
            with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
                await AnthropicProvider(model="m").query("hello")

    @pytest.mark.asyncio
    async def test_agentic_run_returns_final_reply_not_narration(self):
        final = '{"word": "ruler"}'
        out = await self._run([
            self._assistant("Let me check that file.", tool_use=True),
            self._user([SimpleNamespace(tool_use_id="toolu_1", content="file body")]),
            self._assistant("subagent chatter", parent_tool_use_id="toolu_1"),
            self._assistant(final),
            self._result(final),
        ])
        assert out == final

    @pytest.mark.asyncio
    async def test_assistant_fallback_joins_turns_and_skips_subagents(self):
        out = await self._run([
            self._assistant("first"),
            self._assistant("subagent chatter", parent_tool_use_id="toolu_1"),
            self._assistant("second"),
            self._result(None),
        ])
        assert out == "first\nsecond"

    @pytest.mark.asyncio
    async def test_user_message_text_is_not_part_of_reply(self):
        out = await self._run([
            self._user("replayed user prompt"),
            self._assistant("the reply"),
            self._result(None),
        ])
        assert out == "the reply"


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


class TestVisionInputForms:
    """query_with_image accepts bytes OR a path; query_with_images is multi-image.

    Backported from CSP's ClaudeClient during the consolidation (port step 4,
    spiritwriter-core#76 / claude-studio-producer#15).
    """

    @staticmethod
    def _png(tmp_path):
        p = tmp_path / "img.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        return p

    @staticmethod
    def _image_blocks(fake_client):
        content = fake_client.messages.create.call_args.kwargs["messages"][0]["content"]
        return [b for b in content if b["type"] == "image"]

    @pytest.mark.asyncio
    async def test_query_with_image_accepts_path(self, tmp_path):
        provider = AnthropicProvider(model="m")
        fake_anthropic, fake_client = _make_fake_anthropic("desc")
        with patch.dict("sys.modules", {"anthropic": fake_anthropic}), \
             patch("spiritwriter.llm.anthropic.get_api_key", return_value="k"):
            out = await provider.query_with_image("p", self._png(tmp_path))
        assert out == "desc"
        blocks = self._image_blocks(fake_client)
        assert len(blocks) == 1
        assert blocks[0]["source"]["media_type"] == "image/png"

    @pytest.mark.asyncio
    async def test_query_with_image_path_kwarg_alias(self, tmp_path):
        provider = AnthropicProvider(model="m")
        fake_anthropic, fake_client = _make_fake_anthropic("desc")
        with patch.dict("sys.modules", {"anthropic": fake_anthropic}), \
             patch("spiritwriter.llm.anthropic.get_api_key", return_value="k"):
            out = await provider.query_with_image("p", image_path=self._png(tmp_path))
        assert out == "desc"
        assert len(self._image_blocks(fake_client)) == 1

    @pytest.mark.asyncio
    async def test_query_with_image_bytes_still_works(self):
        provider = AnthropicProvider(model="m")
        fake_anthropic, fake_client = _make_fake_anthropic("desc")
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        with patch.dict("sys.modules", {"anthropic": fake_anthropic}), \
             patch("spiritwriter.llm.anthropic.get_api_key", return_value="k"):
            await provider.query_with_image("p", png)
        blocks = self._image_blocks(fake_client)
        assert len(blocks) == 1
        assert blocks[0]["source"]["media_type"] == "image/png"

    @pytest.mark.asyncio
    async def test_query_with_images_multi_mixed(self, tmp_path):
        provider = AnthropicProvider(model="m")
        fake_anthropic, fake_client = _make_fake_anthropic("multi")
        images = [
            b"\xff\xd8\xff\x00jpeg-ish",                    # raw bytes
            self._png(tmp_path),                            # path
            {"data": "Zm9v", "media_type": "image/webp"},   # pre-formatted dict
        ]
        with patch.dict("sys.modules", {"anthropic": fake_anthropic}), \
             patch("spiritwriter.llm.anthropic.get_api_key", return_value="k"):
            out = await provider.query_with_images("p", images)
        assert out == "multi"
        blocks = self._image_blocks(fake_client)
        assert len(blocks) == 3
        assert blocks[2]["source"]["media_type"] == "image/webp"

    @pytest.mark.asyncio
    async def test_query_with_image_missing_path_raises(self, tmp_path):
        provider = AnthropicProvider(model="m")
        with pytest.raises(FileNotFoundError):
            await provider.query_with_image("p", tmp_path / "nope.png")

    @pytest.mark.asyncio
    async def test_query_with_image_no_image_raises(self):
        provider = AnthropicProvider(model="m")
        with pytest.raises(ValueError):
            await provider.query_with_image("p")

    @pytest.mark.asyncio
    async def test_query_with_images_empty_raises(self):
        provider = AnthropicProvider(model="m")
        with pytest.raises(ValueError):
            await provider.query_with_images("p", [])

    @pytest.mark.asyncio
    async def test_preformatted_dict_missing_data_raises(self):
        provider = AnthropicProvider(model="m")
        with pytest.raises(ValueError):
            await provider.query_with_images("p", [{"media_type": "image/png"}])

    @pytest.mark.asyncio
    async def test_vision_return_usage_tuple(self):
        provider = AnthropicProvider(model="m")
        fake_anthropic, fake_client = _make_fake_anthropic("desc")
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        with patch.dict("sys.modules", {"anthropic": fake_anthropic}), \
             patch("spiritwriter.llm.anthropic.get_api_key", return_value="k"):
            out = await provider.query_with_image("p", png, return_usage=True)
        assert isinstance(out, tuple) and len(out) == 2
        text, usage = out
        assert text == "desc"
        assert usage["total_tokens"] == 2

    @pytest.mark.asyncio
    async def test_system_prompt_reaches_create_kwargs(self):
        provider = AnthropicProvider(model="m")
        fake_anthropic, fake_client = _make_fake_anthropic("desc")
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        with patch.dict("sys.modules", {"anthropic": fake_anthropic}), \
             patch("spiritwriter.llm.anthropic.get_api_key", return_value="k"):
            await provider.query_with_image("p", png, system_prompt="be terse")
        assert fake_client.messages.create.call_args.kwargs["system"] == "be terse"


class TestMockProviderVisionContract:
    """The mock must honor the same vision contract as AnthropicProvider, so
    CSP's mock-mode tests don't AttributeError/TypeError on the new forms."""

    @pytest.mark.asyncio
    async def test_mock_query_with_image_path_alias(self):
        mock = MockLLMProvider("ok")
        assert await mock.query_with_image("p", image_path="whatever.png") == "ok"

    @pytest.mark.asyncio
    async def test_mock_query_with_images(self):
        mock = MockLLMProvider("ok")
        assert await mock.query_with_images("p", [b"a", b"b"]) == "ok"
