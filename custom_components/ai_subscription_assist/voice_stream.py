"""Client for Anthropic's speech-to-text voice stream.

This speaks the same WebSocket protocol that the Claude Code CLI uses for its
``/voice`` dictation feature. The endpoint is not part of Anthropic's public
API and is not documented; everything here was derived from the shipped CLI
client (v2.1.223) and may change without notice.

Protocol summary:

* Connect to ``wss://api.anthropic.com/api/ws/speech_to_text/voice_stream``
  with the audio parameters in the query string and a Claude.ai OAuth bearer
  token in the ``Authorization`` header. API keys are rejected: the service is
  only available to Claude.ai account authentication.
* Immediately send ``{"type": "KeepAlive"}``, then repeat it every 8 seconds.
* Send audio as binary frames of raw signed 16-bit little-endian PCM,
  16 kHz, mono.
* Send ``{"type": "CloseStream"}`` to flush the final transcript.
* Server frames are JSON:
    - ``TranscriptInterim`` / ``TranscriptText``: ``data`` replaces the
      current in-progress segment.
    - ``TranscriptEndpoint``: the pending segment is final.
    - ``TranscriptError`` / ``error``: transcription failed.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
from contextlib import suppress
from dataclasses import dataclass, field
import json
from urllib.parse import urlencode

import aiohttp

from .const import LOGGER

VOICE_STREAM_BASE_URL = "wss://api.anthropic.com"
VOICE_STREAM_PATH = "/api/ws/speech_to_text/voice_stream"

# Mirrors the CLI's recorder configuration. The service is told the format up
# front, so these must match the PCM actually sent on the wire.
AUDIO_ENCODING = "linear16"
AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1

# Client identification, copied from the CLI so the service sees a request
# shape it recognises.
CLIENT_USER_AGENT = "claude-cli/2.1.223 (external, cli)"
CLIENT_APP = "cli"
CLIENT_PLATFORM = "claude_code_cli"

KEEPALIVE_FRAME = '{"type":"KeepAlive"}'
CLOSE_STREAM_FRAME = '{"type":"CloseStream"}'
KEEPALIVE_INTERVAL = 8.0

# Finalisation timeouts, matching the CLI's FINALIZE_TIMEOUTS_MS.
FINALIZE_SAFETY_TIMEOUT = 5.0
FINALIZE_NO_DATA_TIMEOUT = 1.5

# Maximum length of the x-config-keyterms header value.
KEYTERMS_MAX_LENGTH = 1024

# The service truncates roughly half a second at each end of a stream. Voice
# activity detection hands over audio that starts and stops right at speech,
# so without padding the first and last words get eaten. Both were verified
# against the live service: unpadded "Turn off the kitchen lights ... twenty
# one degrees" came back missing its final "s", and Thai lost its first word
# entirely.
PAD_SILENCE_SECONDS = 0.5
PAD_SILENCE = b"\x00\x00" * int(AUDIO_SAMPLE_RATE * PAD_SILENCE_SECONDS)

# Languages the service transcribes correctly. The first block is the set the
# Claude Code documentation lists; the second was verified by hand against the
# live service, which accepts more than Anthropic documents.
#
# Codes outside this list are NOT rejected - the service silently falls back to
# transcribing as English, which yields confident nonsense rather than an
# error. So only add a code here after checking it against real audio.
SUPPORTED_LANGUAGES = [
    # Documented.
    "cs",
    "da",
    "de",
    "el",
    "en",
    "es",
    "fr",
    "hi",
    "id",
    "it",
    "ja",
    "ko",
    "nl",
    "no",
    "pl",
    "pt",
    "ru",
    "sv",
    "tr",
    "uk",
    # Undocumented, verified working.
    "ar",
    "ca",
    "fi",
    "he",
    "hu",
    "ro",
    "th",
    "vi",
    "zh",
]


class VoiceStreamError(Exception):
    """Raised when the transcription service reports a failure."""


def sanitize_keyterms(keyterms: list[str]) -> str:
    """Build the x-config-keyterms header value.

    Mirrors the CLI's sanitizer: commas become spaces, non-ASCII is dropped,
    whitespace is collapsed, duplicates are removed, and the joined value is
    capped at KEYTERMS_MAX_LENGTH.
    """
    seen: set[str] = set()
    out: list[str] = []
    length = 0

    for raw in keyterms:
        term = raw.replace(",", " ")
        term = "".join(ch for ch in term if " " <= ch <= "~")
        term = " ".join(term.split()).strip()
        if not term or term in seen:
            continue
        added = len(term) + (1 if out else 0)
        if length + added > KEYTERMS_MAX_LENGTH:
            break
        seen.add(term)
        out.append(term)
        length += added

    return ",".join(out)


def normalize_language(language: str | None) -> str:
    """Map a Home Assistant language tag onto a supported dictation code."""
    if not language:
        return "en"
    code = language.replace("_", "-").split("-", 1)[0].lower()
    if code in SUPPORTED_LANGUAGES:
        return code
    return "en"


def build_url(language: str) -> str:
    """Build the voice stream URL, including the CLI's query parameters."""
    params = {
        "encoding": AUDIO_ENCODING,
        "sample_rate": str(AUDIO_SAMPLE_RATE),
        "channels": str(AUDIO_CHANNELS),
        "endpointing_ms": "300",
        "utterance_end_ms": "1000",
        "language": language,
        "use_conversation_engine": "true",
        "stt_provider": "deepgram-nova3",
    }
    return f"{VOICE_STREAM_BASE_URL}{VOICE_STREAM_PATH}?{urlencode(params)}"


def build_headers(access_token: str, keyterms: str | None = None) -> dict[str, str]:
    """Build the WebSocket upgrade headers."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": CLIENT_USER_AGENT,
        "x-app": CLIENT_APP,
        "anthropic-client-platform": CLIENT_PLATFORM,
    }
    if keyterms:
        headers["x-config-keyterms"] = keyterms
    return headers


@dataclass(slots=True)
class _TranscriptAccumulator:
    """Track interim and finalised transcript segments.

    The service replaces (rather than appends to) the in-progress segment on
    every interim frame, and promotes it to final on an endpoint frame.
    """

    finals: list[str] = field(default_factory=list)
    pending: str = ""

    def interim(self, text: str) -> None:
        self.pending = text

    def endpoint(self) -> None:
        if self.pending:
            self.finals.append(self.pending)
            self.pending = ""

    def text(self) -> str:
        parts = [*self.finals]
        if self.pending:
            parts.append(self.pending)
        return " ".join(part.strip() for part in parts if part.strip())


async def _async_wait_for_finalize(
    finalized: asyncio.Event, data_after_close: asyncio.Event
) -> None:
    """Wait for the service to flush the last transcript after CloseStream."""
    finalize_task = asyncio.create_task(finalized.wait())
    data_task = asyncio.create_task(data_after_close.wait())
    try:
        done, _ = await asyncio.wait(
            {finalize_task, data_task},
            timeout=FINALIZE_NO_DATA_TIMEOUT,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if finalize_task in done:
            return
        if not done:
            LOGGER.debug("voice_stream: nothing returned after CloseStream")
            return
        # Data is still flowing, so wait out the remaining safety window.
        with suppress(TimeoutError):
            await asyncio.wait_for(
                finalized.wait(),
                timeout=FINALIZE_SAFETY_TIMEOUT - FINALIZE_NO_DATA_TIMEOUT,
            )
    finally:
        for task in (finalize_task, data_task):
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


async def async_transcribe(
    session: aiohttp.ClientSession,
    access_token: str,
    audio: AsyncIterable[bytes],
    *,
    language: str = "en",
    keyterms: list[str] | None = None,
) -> str:
    """Stream PCM audio to the voice service and return the transcript.

    ``audio`` must yield raw signed 16-bit little-endian PCM at 16 kHz, mono.
    Raises VoiceStreamError if the service reports a transcription error or
    the connection cannot be established.
    """
    url = build_url(language)
    headers = build_headers(access_token, sanitize_keyterms(keyterms or []) or None)

    accumulator = _TranscriptAccumulator()
    error: str | None = None
    # Endpoint frames also arrive mid-utterance, at every pause, so only the
    # ones seen after CloseStream mean the service has flushed everything.
    close_sent = False
    finalized = asyncio.Event()
    data_after_close = asyncio.Event()

    try:
        ws = await session.ws_connect(url, headers=headers, heartbeat=None)
    except aiohttp.WSServerHandshakeError as err:
        raise VoiceStreamError(
            f"Voice stream upgrade rejected with HTTP {err.status}"
        ) from err
    except aiohttp.ClientError as err:
        raise VoiceStreamError(f"Voice stream connection failed: {err}") from err

    async def _keepalive() -> None:
        """Send the periodic KeepAlive the service expects."""
        try:
            while True:
                await asyncio.sleep(KEEPALIVE_INTERVAL)
                if ws.closed:
                    return
                await ws.send_str(KEEPALIVE_FRAME)
        except (asyncio.CancelledError, ConnectionResetError):
            raise
        except Exception:  # noqa: BLE001 - keepalive must never kill the stream
            LOGGER.debug("voice_stream: keepalive stopped", exc_info=True)

    async def _receive() -> None:
        """Consume server frames until the socket closes."""
        nonlocal error

        async for msg in ws:
            if msg.type is not aiohttp.WSMsgType.TEXT:
                continue
            try:
                frame = json.loads(msg.data)
            except ValueError:
                continue

            frame_type = frame.get("type")
            if frame_type in ("TranscriptInterim", "TranscriptText"):
                data = frame.get("data")
                if data:
                    accumulator.interim(data)
                if close_sent:
                    data_after_close.set()
            elif frame_type == "TranscriptEndpoint":
                accumulator.endpoint()
                if close_sent:
                    finalized.set()
            elif frame_type == "TranscriptError":
                error = (
                    frame.get("description")
                    or frame.get("error_code")
                    or "unknown transcription error"
                )
                finalized.set()
                return
            elif frame_type == "error":
                error = frame.get("message") or "unstructured error frame"
                finalized.set()
                return

    keepalive_task = asyncio.create_task(_keepalive())
    receive_task = asyncio.create_task(_receive())

    try:
        await ws.send_str(KEEPALIVE_FRAME)
        # Absorb the start-of-stream truncation so the first word survives.
        await ws.send_bytes(PAD_SILENCE)

        sent_any = False
        async for chunk in audio:
            if not chunk:
                continue
            if ws.closed:
                break
            await ws.send_bytes(chunk)
            sent_any = True

        if not sent_any:
            raise VoiceStreamError("No audio was captured")

        if not ws.closed:
            # And the end-of-stream truncation, so the last word survives.
            await ws.send_bytes(PAD_SILENCE)
            close_sent = True
            await ws.send_str(CLOSE_STREAM_FRAME)

        # Mirrors the CLI's two timers: give up quickly if nothing at all comes
        # back after CloseStream, but allow the longer safety window once the
        # service starts flushing.
        await _async_wait_for_finalize(finalized, data_after_close)
    finally:
        for task in (keepalive_task, receive_task):
            task.cancel()
        if not ws.closed:
            await ws.close()
        for task in (keepalive_task, receive_task):
            with suppress(asyncio.CancelledError, Exception):
                await task

    if error:
        raise VoiceStreamError(error)

    return accumulator.text()
