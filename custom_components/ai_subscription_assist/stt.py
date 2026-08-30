"""Speech-to-text platform backed by Anthropic's voice stream service.

Only available on Claude subscription OAuth entries: the dictation service
rejects API-key authentication.
"""

from __future__ import annotations

from collections.abc import AsyncIterable
import time

from homeassistant.components.stt import (
    AudioBitRates,
    AudioChannels,
    AudioCodecs,
    AudioFormats,
    AudioSampleRates,
    SpeechMetadata,
    SpeechResult,
    SpeechResultState,
    SpeechToTextEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_EXPIRES_AT,
    CONF_PROVIDER,
    LOGGER,
    PROVIDER_CLAUDE_OAUTH,
)
from .voice_stream import (
    SUPPORTED_LANGUAGES,
    VoiceStreamError,
    async_transcribe,
    normalize_language,
)

# Refresh the access token if it expires within this many seconds.
TOKEN_EXPIRY_MARGIN = 600

# Cap on how many entity names are sent as recognition hints.
MAX_KEYTERMS = 100


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up speech-to-text for a Claude subscription entry."""
    if config_entry.data.get(CONF_PROVIDER) != PROVIDER_CLAUDE_OAUTH:
        # Other providers have no equivalent transcription endpoint.
        return

    async_add_entities([ClaudeVoiceStreamEntity(config_entry)])


def _exposed_entity_names(hass: HomeAssistant) -> list[str]:
    """Collect names of Assist-exposed entities to use as recognition hints.

    The CLI passes project and branch names as keyterms; the equivalent here
    is the entity and area names the user is most likely to say.
    """
    try:
        from homeassistant.components import conversation
        from homeassistant.components.homeassistant import async_should_expose
    except ImportError:
        return []

    names: list[str] = []
    for state in hass.states.async_all():
        if len(names) >= MAX_KEYTERMS:
            break
        try:
            if not async_should_expose(hass, conversation.DOMAIN, state.entity_id):
                continue
        except Exception:  # noqa: BLE001 - hints are best-effort only
            continue
        friendly_name = state.attributes.get("friendly_name")
        if isinstance(friendly_name, str) and friendly_name:
            names.append(friendly_name)

    try:
        from homeassistant.helpers import area_registry as ar

        names.extend(area.name for area in ar.async_get(hass).async_list_areas())
    except Exception:  # noqa: BLE001 - hints are best-effort only
        pass

    return names


class ClaudeVoiceStreamEntity(SpeechToTextEntity):
    """Transcribe Assist audio using the Claude subscription voice service."""

    _attr_has_entity_name = True
    _attr_name = "Claude speech-to-text"

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize."""
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_stt"

    @property
    def supported_languages(self) -> list[str]:
        """Return the languages the dictation service accepts."""
        return SUPPORTED_LANGUAGES

    @property
    def supported_formats(self) -> list[AudioFormats]:
        """Return supported formats."""
        return [AudioFormats.WAV]

    @property
    def supported_codecs(self) -> list[AudioCodecs]:
        """Return supported codecs."""
        return [AudioCodecs.PCM]

    @property
    def supported_bit_rates(self) -> list[AudioBitRates]:
        """Return supported bit rates."""
        return [AudioBitRates.BITRATE_16]

    @property
    def supported_sample_rates(self) -> list[AudioSampleRates]:
        """Return supported sample rates."""
        return [AudioSampleRates.SAMPLERATE_16000]

    @property
    def supported_channels(self) -> list[AudioChannels]:
        """Return supported channel counts."""
        return [AudioChannels.CHANNEL_MONO]

    async def _async_access_token(self) -> str | None:
        """Return a valid access token, refreshing it if it is near expiry."""
        entry = self._config_entry
        expires_at = float(entry.data.get(CONF_EXPIRES_AT, 0))

        if time.time() > (expires_at - TOKEN_EXPIRY_MARGIN):
            from . import _async_refresh_token

            token = await _async_refresh_token(self.hass, entry)
            if token:
                return token
            LOGGER.error("Could not refresh the OAuth token for speech-to-text")

        token = entry.data.get(CONF_ACCESS_TOKEN)
        return str(token) if token else None

    async def async_process_audio_stream(
        self, metadata: SpeechMetadata, stream: AsyncIterable[bytes]
    ) -> SpeechResult:
        """Transcribe an Assist audio stream."""
        language = normalize_language(metadata.language)

        access_token = await self._async_access_token()
        if not access_token:
            return SpeechResult(None, SpeechResultState.ERROR)

        try:
            text = await async_transcribe(
                async_get_clientsession(self.hass),
                access_token,
                stream,
                language=language,
                keyterms=_exposed_entity_names(self.hass),
            )
        except VoiceStreamError as err:
            LOGGER.error("Speech-to-text failed: %s", err)
            return SpeechResult(None, SpeechResultState.ERROR)
        except Exception:  # noqa: BLE001 - never break the Assist pipeline
            LOGGER.exception("Unexpected speech-to-text failure")
            return SpeechResult(None, SpeechResultState.ERROR)

        if not text:
            LOGGER.debug("Speech-to-text returned no transcript")
            return SpeechResult(None, SpeechResultState.ERROR)

        return SpeechResult(text, SpeechResultState.SUCCESS)
