#
# Copyright (c) 2024–2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Unit test: VLLMOpenAILLMService defers TTFB to the first non-thinking token."""

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipecat.services.openai.llm import OpenAILLMService  # noqa: E402

from nemotron_llm import VLLMOpenAILLMService  # noqa: E402


def _chunk(*, content=None, tool_calls=None, reasoning_content=None, role=None):
    delta = types.SimpleNamespace(content=content, tool_calls=tool_calls)
    if reasoning_content is not None:
        delta.reasoning_content = reasoning_content
    if role is not None:
        delta.role = role
    return types.SimpleNamespace(choices=[types.SimpleNamespace(delta=delta)])


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks
        self.closed = False

    def __aiter__(self):
        async def gen():
            for c in self._chunks:
                yield c

        return gen()

    async def close(self):
        self.closed = True


def test_ttfb_armed_only_on_first_content_token():
    async def run():
        svc = VLLMOpenAILLMService(model="m", api_key="EMPTY", base_url="http://x/v1")
        # Stream: role-only, reasoning-only, empty, then the first real content token.
        upstream = _FakeStream(
            [
                _chunk(role="assistant"),
                _chunk(reasoning_content="let me think..."),
                _chunk(content=None),
                _chunk(content="Hello"),
                _chunk(content=" there"),
            ]
        )

        stop_calls = []
        with (
            patch.object(
                OpenAILLMService, "get_chat_completions", new=AsyncMock(return_value=upstream)
            ),
            patch.object(
                OpenAILLMService,
                "stop_ttfb_metrics",
                new=AsyncMock(side_effect=lambda **kw: stop_calls.append(True)),
            ),
        ):
            wrapped = await svc.get_chat_completions(context=None)

            armed_history = []
            async for chunk in wrapped:
                # Simulate pipecat's per-chunk stop_ttfb_metrics() call (base_llm.py:467).
                await svc.stop_ttfb_metrics()
                armed_history.append(svc._ttft_armed)

        # role-only, reasoning-only, empty -> not armed; arms at first content ("Hello").
        assert armed_history == [False, False, False, True, True]
        # Underlying stop_ttfb_metrics only fired once armed (2 content chunks).
        assert len(stop_calls) == 2
        # The wrapper closed the underlying stream.
        assert upstream.closed is True

    asyncio.run(run())


def test_no_content_turn_never_stops_ttfb():
    """A turn with only reasoning/role/empty deltas must not record TTFB."""

    async def run():
        svc = VLLMOpenAILLMService(model="m", api_key="EMPTY", base_url="http://x/v1")
        upstream = _FakeStream(
            [
                _chunk(role="assistant"),
                _chunk(reasoning_content="thinking, no answer emitted"),
            ]
        )
        stop_calls = []
        with (
            patch.object(
                OpenAILLMService, "get_chat_completions", new=AsyncMock(return_value=upstream)
            ),
            patch.object(
                OpenAILLMService,
                "stop_ttfb_metrics",
                new=AsyncMock(side_effect=lambda **kw: stop_calls.append(True)),
            ),
        ):
            wrapped = await svc.get_chat_completions(context=None)
            async for _chunk_ in wrapped:
                await svc.stop_ttfb_metrics()
        assert svc._ttft_armed is False
        assert stop_calls == []

    asyncio.run(run())


def test_arm_resets_per_turn():
    async def run():
        svc = VLLMOpenAILLMService(model="m", api_key="EMPTY", base_url="http://x/v1")
        svc._ttft_armed = True  # leftover from a prior turn
        with patch.object(
            OpenAILLMService,
            "get_chat_completions",
            new=AsyncMock(return_value=_FakeStream([_chunk(reasoning_content="x")])),
        ):
            wrapped = await svc.get_chat_completions(context=None)
            # get_chat_completions resets the flag before streaming.
            assert svc._ttft_armed is False
            async for _ in wrapped:
                pass
        assert svc._ttft_armed is False  # no content this turn

    asyncio.run(run())
