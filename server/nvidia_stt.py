#
# Copyright (c) 2024–2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""NVIDIA Parakeet streaming speech-to-text service implementation."""

import asyncio
import json
import time
from collections.abc import AsyncGenerator

import websockets
from loguru import logger
from pipecat.audio.utils import create_stream_resampler
from pipecat.frames.frames import (
    AudioRawFrame,
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    InterimTranscriptionFrame,
    StartFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import WebsocketSTTService
from pipecat.utils.time import time_now_iso8601


def _strip_committed_prefix(interim_text: str, committed_count: int) -> str | None:
    """Strip already-finalized tokens from a cumulative interim transcript.

    The server emits interims as the full cumulative hypothesis since the
    connection opened: ``[ already-finalized prior tokens ] + [ new this turn ]``.
    ``committed_count`` is how many tokens we have already emitted across finals,
    so the current turn's new text is the interim with that many leading tokens
    removed.

    We strip purely by token COUNT (not by value) on purpose: the server revises
    the last committed word(s) when it keeps decoding past a forced finalization
    (e.g. a hard-reset artifact ``"ZAC."`` that the next interim corrects to
    ``"zest."``). The token *count* of the prior region is preserved across that
    revision even though the text is not, and a value-based prefix check would
    both fail on every turn boundary and accumulate stale artifact tokens.

    Returns the current-turn tail, ``""`` if nothing new yet, or ``None`` when the
    interim is shorter than the committed prefix (only possible without a
    cumulative session, e.g. an unexpected server-side reset — caller emits
    the interim unchanged rather than slicing wrongly).

    Best-effort cosmetic: if the server ever re-tokenizes the prior region
    (splits/merges a finalized word so its token count changes, e.g. "ice cream"
    -> "icecream"), strip-by-count can drop or keep one boundary word in the
    interim. This is interim-display only; FINAL frames are never altered.
    """
    interim_tokens = interim_text.split()
    if len(interim_tokens) < committed_count:
        return None
    return " ".join(interim_tokens[committed_count:])


class NVidiaWebSocketSTTService(WebsocketSTTService):
    """NVIDIA Parakeet streaming speech-to-text service.

    Provides real-time speech recognition using NVIDIA's Parakeet ASR model
    via WebSocket. Supports interim results for responsive transcription.

    Turn finalization is driven by ``VADUserStoppedSpeakingFrame``: when VAD
    detects end-of-speech, this service sends a "hard" reset that asks the
    server to inject finalization silence and return the final transcript as
    quickly as possible. That final is emitted as a ``finalized=True``
    ``TranscriptionFrame``, which lets a turn-analyzer stop strategy
    (e.g. ``TurnAnalyzerUserTurnStopStrategy``) end the user turn immediately
    instead of falling back to its ``user_turn_stop_timeout``.

    The server expects:
    - Audio: 16-bit PCM, 16kHz, mono
    - Reset signal: {"type": "reset", "finalize": true/false}

    The server sends:
    - Ready: {"type": "ready"}
    - Transcript: {"type": "transcript", "text": "...", "is_final": true/false}
    """

    def __init__(
        self,
        *,
        url: str = "ws://localhost:8080",
        sample_rate: int = 16000,
        strip_interim_prefix: bool = False,
        preroll_seconds: float = 1.0,
        ws_ping_interval: float = 20.0,
        ws_ping_timeout: float = 20.0,
        **kwargs,
    ):
        """Initialize the NVIDIA STT service.

        Args:
            url: WebSocket URL of the NVIDIA ASR server.
            sample_rate: Audio sample rate (must be 16000 for Parakeet).
            strip_interim_prefix: Strip already-finalized tokens from cumulative
                interims (per-turn interim display). Default False — enable ONLY
                against a server that emits cumulative interims (the continuous
                Nemotron mode confirmed for this deployment); enabling it against a
                server that cold-resets per turn would wrongly strip repeated text.
            preroll_seconds: Audio buffered before VAD start so speech onsets are preserved.
            ws_ping_interval: WebSocket ping interval in seconds.
            ws_ping_timeout: WebSocket ping timeout in seconds.
            **kwargs: Additional arguments passed to the parent WebsocketSTTService.
        """
        # model/language are unsupported here (the server has a fixed model and we
        # don't do runtime language selection) — set them to None so the base
        # STTSettings validator doesn't flag them as NOT_GIVEN.
        super().__init__(
            sample_rate=sample_rate,
            settings=STTSettings(model=None, language=None),
            **kwargs,
        )
        self._url = url
        self._strip_interim_prefix = strip_interim_prefix
        self._preroll_seconds = preroll_seconds
        # Parakeet requires 16kHz mono PCM. Transport audio may arrive at a
        # different rate (e.g. 48kHz from WebRTC), so resample on the way in.
        # The SOXR stream resampler keeps history across chunks to avoid clicks
        # at chunk boundaries, which matters for streaming ASR quality.
        self._resampler = create_stream_resampler()
        self._ws_ping_interval = ws_ping_interval
        self._ws_ping_timeout = ws_ping_timeout
        self._websocket = None
        self._receive_task: asyncio.Task | None = None
        self._ready = False
        self._committed_token_count: int = 0
        self._user_speaking = False
        self._audio_ring = bytearray()
        self._preroll_bytes = 0
        # Lock to ensure any in-progress audio send completes before reset
        self._audio_send_lock = asyncio.Lock()
        # Functional: unmatched VAD-stop guard reads bytes sent since last reset.
        self._audio_bytes_sent = 0

        # Set when we send the finalization (hard) reset on VAD silence, and
        # cleared once the matching final transcript arrives.
        self._waiting_for_final: bool = False

    def can_generate_metrics(self) -> bool:
        """Check if this service can generate processing metrics."""
        return True

    @property
    def supports_ttfs(self) -> bool:
        """TTFS doesn't apply: the server defines turn boundaries directly."""
        return False

    async def start(self, frame: StartFrame):
        """Start the NVIDIA STT service.

        Args:
            frame: The start frame containing initialization parameters.
        """
        await super().start(frame)
        self._preroll_bytes = int(self.sample_rate * self._preroll_seconds) * 2
        await self._connect()

    async def stop(self, frame: EndFrame):
        """Stop the NVIDIA STT service.

        Args:
            frame: The end frame.
        """
        # Discard pre-VAD audio; session-end finalization is best-effort because
        # stop disconnects without waiting for the final transcript.
        self._audio_ring.clear()
        # Send HARD reset to ensure any buffered audio is transcribed
        await self._send_reset(finalize=True)
        await super().stop(frame)
        await self._disconnect()

    async def cancel(self, frame: CancelFrame):
        """Cancel the NVIDIA STT service.

        Args:
            frame: The cancel frame.
        """
        # Stop the receive task first so the bounded manual drain below is the
        # only coroutine reading from the websocket.
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None

        # Discard pre-VAD audio; live speech has already been streamed.
        self._audio_ring.clear()

        # Send HARD reset to capture any remaining buffered audio
        # This ensures words at the end of audio aren't lost when pipeline is cancelled
        await self._send_reset(finalize=True)

        # Wait briefly for server to process the reset and send response
        # Without this, we disconnect before receiving the final transcript
        if self._websocket and self._ready:
            try:
                msg = await asyncio.wait_for(self._websocket.recv(), timeout=0.5)
                data = json.loads(msg)
                if data.get("type") == "transcript" and data.get("is_final"):
                    await self._handle_transcript(data)
            except (TimeoutError, Exception):
                pass  # Best effort - don't block cancel on network issues
        await super().cancel(frame)
        await self._disconnect()

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        """Send audio data to NVIDIA ASR server for transcription.

        Args:
            audio: Raw audio bytes (16-bit PCM, 16kHz, mono).

        Yields:
            Frame: None (transcription results come via WebSocket receive task).
        """
        if self._websocket and self._ready:
            try:
                async with self._audio_send_lock:
                    await self._websocket.send(audio)
                    self._audio_bytes_sent += len(audio)
            except Exception as e:
                logger.error(f"{self} failed to send audio: {e}")
                await self._report_error(ErrorFrame(f"Failed to send audio: {e}"))
        yield None

    async def process_audio_frame(self, frame: AudioRawFrame, direction: FrameDirection):
        """Gate websocket audio sends on VAD while preserving audio passthrough."""
        if self._reconnecting:
            self._reconnect_audio_buffer.append((frame, direction))
            return

        if self._muted:
            return

        # UserAudioRawFrame contains a user_id (e.g. Daily, Livekit)
        if hasattr(frame, "user_id"):
            self._user_id = frame.user_id
        # AudioRawFrame does not have a user_id (e.g. SmallWebRTCTransport, websockets)
        else:
            self._user_id = ""

        self._last_audio_time = time.monotonic()

        if not frame.audio:
            # Ignoring in case we don't have audio to transcribe.
            logger.warning(
                f"Empty audio frame received for STT service: {self.name} {frame.num_frames}"
            )
            return

        # Resample to the rate Parakeet requires (self.sample_rate) before the
        # audio enters the preroll ring or is streamed; everything downstream
        # assumes 16kHz mono PCM. resample() is a no-op when rates already match.
        audio = await self._resampler.resample(frame.audio, frame.sample_rate, self.sample_rate)

        if self._user_speaking:
            await self.process_generator(self.run_stt(audio))
            return

        self._audio_ring += audio
        if self._preroll_bytes > 0 and len(self._audio_ring) > self._preroll_bytes:
            del self._audio_ring[: -self._preroll_bytes]

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process frames, finalizing the utterance on VAD end-of-speech.

        Finalization is driven by ``VADUserStoppedSpeakingFrame``: when VAD
        detects end-of-speech we send a hard reset so the server injects
        finalization silence and returns the final transcript quickly. The
        resulting ``finalized=True`` ``TranscriptionFrame`` lets a downstream
        turn-analyzer stop strategy end the user turn without waiting on its
        ``user_turn_stop_timeout``.

        Args:
            frame: The frame to process.
            direction: The direction of frame processing.
        """
        # A new turn is starting; reset finalization state.
        if isinstance(frame, UserStartedSpeakingFrame):
            self._waiting_for_final = False
            await super().process_frame(frame, direction)
            return

        if isinstance(frame, VADUserStartedSpeakingFrame):
            if self._audio_ring:
                await self.process_generator(self.run_stt(bytes(self._audio_ring)))
            self._audio_ring.clear()
            self._user_speaking = True
            await super().process_frame(frame, direction)
            return

        if isinstance(frame, VADUserStoppedSpeakingFrame):
            audio_was_streamed = self._audio_bytes_sent > 0
            self._user_speaking = False

            if not audio_was_streamed:
                self._audio_ring.clear()
                logger.debug(f"{self} ignoring unmatched VAD stop; no audio streamed")
                await self.push_frame(frame, direction)
                return

            # VAD stop must reach the turn analyzer before our final transcript
            # does; base STTService also starts its speech-end TTFB window here.
            await super().process_frame(frame, direction)
            self._waiting_for_final = True
            # Report the standard TTFB metric as our finalization latency:
            # start the clock now (when we ask the server to finalize). The base
            # STTService stops it when our finalized=True TranscriptionFrame is
            # pushed downstream, so TTFB == hard-reset -> final-transcript. This
            # overrides the base class's default speech-end start point.
            await self.start_ttfb_metrics()
            await self._send_reset(finalize=True)
            return

        # All other frames pass through normally.
        await super().process_frame(frame, direction)

    async def _send_reset(self, finalize: bool = True):
        """Send reset signal to trigger transcription.

        Args:
            finalize: If True (hard reset), server adds padding and uses
                      keep_all_outputs=True to capture trailing words.
                      If False (soft reset), server returns current text
                      without forcing decoder output.

        Acquires audio_send_lock to ensure any in-progress audio send completes
        before the reset signal is sent.
        """
        if self._websocket and self._ready:
            try:
                async with self._audio_send_lock:
                    await self._websocket.send(json.dumps({"type": "reset", "finalize": finalize}))
                    # Log inside lock to get accurate byte count
                    samples = self._audio_bytes_sent // 2
                    duration_ms = (samples * 1000) // 16000
                    reset_type = "hard" if finalize else "soft"
                    logger.debug(f"{self} sent {reset_type} reset (audio: {duration_ms}ms)")
                    if finalize:
                        self._audio_bytes_sent = 0  # Only reset on hard reset
            except Exception as e:
                logger.error(f"{self} failed to send reset: {e}")

    async def _connect(self):
        """Connect to the NVIDIA ASR service."""
        logger.debug(f"{self} connecting to {self._url}")
        await self._connect_websocket()

        # Start receive task
        self._receive_task = asyncio.create_task(self._receive_task_handler(self._report_error))

        await self._call_event_handler("on_connected", self)

    async def _disconnect(self):
        """Disconnect from the NVIDIA ASR service."""
        logger.debug(f"{self} disconnecting")

        # Cancel receive task
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None

        await self._disconnect_websocket()
        await self._call_event_handler("on_disconnected", self)

    async def _connect_websocket(self):
        """Establish the websocket connection.

        Liveness during VAD-gated silence relies on WS ping/pong: the server
        is aiohttp with autoping=True and has no app-level audio-idle timeout.
        We deliberately never send silence as keepalive. The 20.0/20.0
        defaults match websockets 15.0.1, so behavior is unchanged; the
        explicit params are for belt-and-suspenders discoverability.
        """
        try:
            self._websocket = await websockets.connect(
                self._url,
                ping_interval=self._ws_ping_interval,
                ping_timeout=self._ws_ping_timeout,
            )
            self._ready = False

            # Wait for ready message
            try:
                ready_msg = await asyncio.wait_for(self._websocket.recv(), timeout=5.0)
                data = json.loads(ready_msg)
                if data.get("type") == "ready":
                    self._ready = True
                    logger.info(f"{self} connected and ready")
                else:
                    logger.warning(f"{self} unexpected initial message: {data}")
                    self._ready = True  # Proceed anyway
            except TimeoutError:
                logger.warning(f"{self} timeout waiting for ready message, proceeding anyway")
                self._ready = True

            self._committed_token_count = 0
            self._user_speaking = False
            self._audio_ring.clear()
            self._audio_bytes_sent = 0

        except Exception as e:
            logger.error(f"{self} connection failed: {e}")
            await self._report_error(ErrorFrame(f"Connection failed: {e}"))
            raise

    async def _disconnect_websocket(self):
        """Close the websocket connection."""
        self._ready = False
        self._committed_token_count = 0
        self._user_speaking = False
        self._audio_ring.clear()
        self._audio_bytes_sent = 0
        if self._websocket:
            try:
                await self._websocket.close()
            except Exception as e:
                logger.debug(f"{self} error closing websocket: {e}")
            finally:
                self._websocket = None

    async def _receive_messages(self):
        """Receive and process websocket messages from NVIDIA ASR server."""
        if not self._websocket:
            return

        async for message in self._websocket:
            try:
                data = json.loads(message)
                msg_type = data.get("type")

                if msg_type == "transcript":
                    await self._handle_transcript(data)
                elif msg_type == "error":
                    error_msg = data.get("message", "Unknown error")
                    logger.error(f"{self} server error: {error_msg}")
                    await self._report_error(ErrorFrame(f"Server error: {error_msg}"))
                elif msg_type == "ready":
                    # Server might send another ready message after reset
                    self._ready = True
                    logger.debug(f"{self} server ready")
                else:
                    logger.debug(f"{self} unknown message type: {msg_type}")

            except json.JSONDecodeError as e:
                logger.error(f"{self} invalid JSON: {e}")
            except Exception as e:
                logger.error(f"{self} error processing message: {e}")

    async def _handle_transcript(self, data: dict):
        """Handle a transcript message from the server.

        Final transcripts from HARD resets (finalize=True) are emitted as
        ``finalized=True`` ``TranscriptionFrame``s so a downstream turn-analyzer
        stop strategy can end the user turn immediately. SOFT-reset finals are
        ignored here (only used for timing); interim results are emitted as
        ``InterimTranscriptionFrame``s.

        Args:
            data: The transcript message data.
        """
        text = data.get("text", "")
        is_final = data.get("is_final", False)
        is_hard_reset = data.get("finalize", True)  # Default True for backward compat

        if not text:
            # A hard reset that came back empty still ends the finalization wait.
            if is_final and is_hard_reset:
                self._waiting_for_final = False
            return

        timestamp = time_now_iso8601()

        if is_final:
            # Only emit TranscriptionFrames from HARD reset responses.
            # Soft reset returns partial/stable text quickly but may have incomplete
            # words (e.g., "shipp" instead of "shipping"). Hard reset adds padding
            # and uses keep_all_outputs=True to get complete words.

            reset_type = "hard" if is_hard_reset else "soft"
            logger.debug(
                f"{self} {reset_type} final at {time.time():.3f}: {text[-50:] if len(text) > 50 else text}"
            )

            if is_hard_reset:
                # Server handles deduplication - it sends only the delta (new portion)
                # so we emit directly without client-side deduplication.
                # finalized=True lets TurnAnalyzerUserTurnStopStrategy end the user
                # turn immediately, and makes the base STTService report TTFB (our
                # finalization latency, started on the hard reset) on this frame.
                await self.push_frame(
                    TranscriptionFrame(
                        text,
                        self._user_id,
                        timestamp,
                        language=None,
                        finalized=True,
                    )
                )
                self._waiting_for_final = False
                self._committed_token_count += len(text.split())
        else:
            logger.trace(f"{self} interim: {text[:30]}...")
            if not self._strip_interim_prefix:
                await self.push_frame(
                    InterimTranscriptionFrame(
                        text,
                        self._user_id,
                        timestamp,
                        language=None,
                    )
                )
                return

            stripped = _strip_committed_prefix(text, self._committed_token_count)
            if stripped is None:
                logger.debug(
                    f"{self} interim ({len(text.split())} tokens) shorter than committed "
                    f"({self._committed_token_count} tokens); emitting unchanged"
                )
                stripped = text
            elif stripped == "":
                return

            await self.push_frame(
                InterimTranscriptionFrame(
                    stripped,
                    self._user_id,
                    timestamp,
                    language=None,
                )
            )
