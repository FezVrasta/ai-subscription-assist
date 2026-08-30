"""The AI Subscription Assist integration."""

from __future__ import annotations

from dataclasses import dataclass
import datetime
import json
import urllib.parse
import time

import anthropic
import httpx

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LLM_HASS_API, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_EXPIRES_AT,
    CONF_GOOGLE_PROJECT_ID,
    CONF_OPENAI_API_KEY,
    CONF_OPENAI_BASE_URL,
    CONF_OPENAI_CODEX_ACCOUNT_ID,
    CONF_PROVIDER,
    CONF_REFRESH_TOKEN,
    DEFAULT_GEMINI_CLI_BASE_URL,
    DEFAULT_OPENAI_BASE_URL,
    DEFAULT_OPENAI_CODEX_BASE_URL,
    DOMAIN,
    GOOGLE_GEMINI_CLI_OAUTH_CLIENT_ID,
    GOOGLE_GEMINI_CLI_OAUTH_CLIENT_SECRET,
    GOOGLE_GEMINI_CLI_OAUTH_TOKEN_URL,
    LOGGER,
    OAUTH_BETA_FLAGS,
    OAUTH_CLIENT_ID,
    OAUTH_TOKEN_URL,
    OPENAI_CODEX_OAUTH_CLIENT_ID,
    OPENAI_CODEX_OAUTH_TOKEN_URL,
    PROVIDER_CLAUDE_OAUTH,
    PROVIDER_OPENAI,
    PROVIDER_OPENAI_CODEX,
    PROVIDER_GOOGLE_GEMINI_CLI,
    TOKEN_REFRESH_INTERVAL,
)
from .memory_service import (
    async_remove_memory_service_for_entry,
    async_setup_memory_service_for_entry,
)
from .memory_websocket import async_setup_memory_websocket_api
from .panel import async_setup_memory_panel

PLATFORMS = (Platform.CONVERSATION, Platform.STT)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

@dataclass(slots=True)
class OpenAIClient:
    """Minimal OpenAI (or OpenAI-compatible) HTTP client wrapper."""

    api_key: str
    base_url: str
    http_client: httpx.AsyncClient

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    async def async_validate(self) -> None:
        """Validate the API key by calling the Models endpoint."""
        resp = await self.http_client.get(
            f"{self.base_url}/models",
            headers=self._headers(),
        )
        resp.raise_for_status()


@dataclass(slots=True)
class OpenAICodexClient:
    """Minimal OpenAI Codex (ChatGPT OAuth) HTTP client wrapper."""

    access_token: str
    account_id: str
    base_url: str
    http_client: httpx.AsyncClient

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            # ChatGPT backend expects the account id header.
            "ChatGPT-Account-Id": self.account_id,
            "Accept": "application/json",
            "User-Agent": "HomeAssistant",
        }

    async def async_validate(self) -> None:
        """Validate the access token by calling a lightweight endpoint."""
        resp = await self.http_client.get(
            f"{self.base_url}/wham/usage",
            headers=self._headers(),
        )
        resp.raise_for_status()


@dataclass(slots=True)
class GeminiCliClient:
    """Minimal Google Gemini CLI (Cloud Code Assist) HTTP client wrapper."""

    access_token: str
    project_id: str
    base_url: str
    http_client: httpx.AsyncClient

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "google-api-python-client",
            "X-Goog-Api-Client": "gl-python/3.13",
        }

    async def async_validate(self) -> None:
        """Validate the access token and project id by calling loadCodeAssist."""
        resp = await self.http_client.post(
            f"{self.base_url}/v1internal:loadCodeAssist",
            headers=self._headers(),
            json={
                "cloudaicompanionProject": self.project_id,
                "metadata": {
                    "ideType": "IDE_UNSPECIFIED",
                    "platform": "PLATFORM_UNSPECIFIED",
                    "pluginType": "GEMINI",
                    "duetProject": self.project_id,
                },
            },
        )
        resp.raise_for_status()


type AiSubscriptionAssistRuntimeData = anthropic.AsyncClient | OpenAIClient | OpenAICodexClient | GeminiCliClient
type AiSubscriptionAssistConfigEntry = ConfigEntry[AiSubscriptionAssistRuntimeData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up AI Subscription Assist."""
    async_setup_memory_websocket_api(hass)
    await async_setup_memory_panel(hass)
    return True


async def _async_refresh_token(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Refresh the Anthropic OAuth access token (Claude subscription OAuth).

    Returns the new access token, or None on failure.
    """
    refresh_token = entry.data.get(CONF_REFRESH_TOKEN)
    if not refresh_token:
        LOGGER.error("No refresh token available for token refresh")
        return None

    try:
        async_client = get_async_client(hass)
        response = await async_client.post(
            OAUTH_TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "client_id": OAUTH_CLIENT_ID,
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        token_data = response.json()
    except httpx.HTTPError as err:
        LOGGER.error("Failed to refresh token: %s", err)
        return None

    new_data = {**entry.data}
    new_data[CONF_ACCESS_TOKEN] = token_data["access_token"]
    if "refresh_token" in token_data:
        new_data[CONF_REFRESH_TOKEN] = token_data["refresh_token"]
    expires_in = token_data.get("expires_in", 28800)
    new_data[CONF_EXPIRES_AT] = time.time() + expires_in

    hass.config_entries.async_update_entry(entry, data=new_data)
    LOGGER.debug(
        "Successfully refreshed OAuth token, expires in %s seconds", expires_in
    )
    return token_data["access_token"]


def _decode_jwt_payload(token: str) -> dict[str, object] | None:
    """Decode a JWT payload without verifying signature."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1]
        # Base64url without padding.
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload_bytes = urllib.parse.unquote_to_bytes(payload_b64)
        import base64

        decoded = base64.urlsafe_b64decode(payload_bytes)
        payload = json.loads(decoded.decode("utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        return None
    return None


def _extract_openai_codex_account_id(access_token: str) -> str | None:
    payload = _decode_jwt_payload(access_token)
    if not payload:
        return None
    auth = payload.get("https://api.openai.com/auth")
    if not isinstance(auth, dict):
        return None
    account_id = auth.get("chatgpt_account_id")
    if isinstance(account_id, str) and account_id:
        return account_id
    return None


async def _async_refresh_openai_codex_token(
    hass: HomeAssistant, entry: ConfigEntry
) -> str | None:
    """Refresh the OpenAI Codex OAuth access token.

    Returns the new access token, or None on failure.
    """
    refresh_token = entry.data.get(CONF_REFRESH_TOKEN)
    if not refresh_token:
        LOGGER.error("No refresh token available for OpenAI Codex token refresh")
        return None

    try:
        async_client = get_async_client(hass)
        response = await async_client.post(
            OPENAI_CODEX_OAUTH_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": str(refresh_token),
                "client_id": OPENAI_CODEX_OAUTH_CLIENT_ID,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        token_data = response.json()
    except httpx.HTTPError as err:
        LOGGER.error("Failed to refresh OpenAI Codex token: %s", err)
        return None

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in")
    if not isinstance(access_token, str) or not access_token:
        LOGGER.error("OpenAI token refresh response missing access_token")
        return None
    if not isinstance(refresh_token, str) or not refresh_token:
        LOGGER.error("OpenAI token refresh response missing refresh_token")
        return None
    if not isinstance(expires_in, (int, float)):
        LOGGER.error("OpenAI token refresh response missing expires_in")
        return None

    account_id = _extract_openai_codex_account_id(access_token)
    if not account_id:
        LOGGER.error("Failed to extract ChatGPT account id from refreshed token")
        return None

    new_data = {**entry.data}
    new_data[CONF_ACCESS_TOKEN] = access_token
    new_data[CONF_REFRESH_TOKEN] = refresh_token
    new_data[CONF_EXPIRES_AT] = time.time() + float(expires_in)
    new_data[CONF_OPENAI_CODEX_ACCOUNT_ID] = account_id

    hass.config_entries.async_update_entry(entry, data=new_data)
    LOGGER.debug(
        "Successfully refreshed OpenAI Codex OAuth token, expires in %s seconds",
        expires_in,
    )
    return access_token


async def _async_refresh_gemini_cli_token(
    hass: HomeAssistant, entry: ConfigEntry
) -> str | None:
    """Refresh the Google Gemini CLI OAuth access token.

    Returns the new access token, or None on failure.
    """
    refresh_token = entry.data.get(CONF_REFRESH_TOKEN)
    if not refresh_token:
        LOGGER.error("No refresh token available for Gemini CLI token refresh")
        return None

    try:
        async_client = get_async_client(hass)
        response = await async_client.post(
            GOOGLE_GEMINI_CLI_OAUTH_TOKEN_URL,
            data={
                "client_id": GOOGLE_GEMINI_CLI_OAUTH_CLIENT_ID,
                "client_secret": GOOGLE_GEMINI_CLI_OAUTH_CLIENT_SECRET,
                "refresh_token": str(refresh_token),
                "grant_type": "refresh_token",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        token_data = response.json()
    except httpx.HTTPError as err:
        LOGGER.error("Failed to refresh Gemini CLI token: %s", err)
        return None

    access_token = token_data.get("access_token")
    new_refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in")
    if not isinstance(access_token, str) or not access_token:
        LOGGER.error("Gemini token refresh response missing access_token")
        return None
    if not isinstance(expires_in, (int, float)):
        LOGGER.error("Gemini token refresh response missing expires_in")
        return None

    new_data = {**entry.data}
    new_data[CONF_ACCESS_TOKEN] = access_token
    if isinstance(new_refresh_token, str) and new_refresh_token:
        new_data[CONF_REFRESH_TOKEN] = new_refresh_token
    # Add a small buffer so we refresh before expiry.
    new_data[CONF_EXPIRES_AT] = time.time() + float(expires_in) - 5 * 60

    hass.config_entries.async_update_entry(entry, data=new_data)
    LOGGER.debug(
        "Successfully refreshed Gemini CLI OAuth token, expires in %s seconds",
        expires_in,
    )
    return access_token


def _create_client(hass: HomeAssistant, access_token: str) -> anthropic.AsyncClient:
    """Create an Anthropic async client using OAuth access token.

    Mimics Claude Code's headers exactly — OAuth tokens require specific
    beta flags and headers to be accepted by the Anthropic API.
    """
    return anthropic.AsyncAnthropic(
        api_key=None,
        auth_token=access_token,
        http_client=get_async_client(hass),
        default_headers={
            "anthropic-beta": OAUTH_BETA_FLAGS,
            "user-agent": "claude-cli/2.1.2 (external, cli)",
            "x-app": "cli",
        },
    )


async def _async_validate_oauth_client(client: anthropic.AsyncClient) -> None:
    """Validate OAuth token against an endpoint that is independent of model names."""
    await client.models.list()


async def async_setup_entry(
    hass: HomeAssistant, entry: AiSubscriptionAssistConfigEntry
) -> bool:
    """Set up AI Subscription Assist from a config entry."""
    # Ensure panel/ws are present when an entry is added after all entries were unloaded.
    # These helpers are idempotent.
    async_setup_memory_websocket_api(hass)
    await async_setup_memory_panel(hass)

    provider = entry.data.get(CONF_PROVIDER, PROVIDER_CLAUDE_OAUTH)

    if provider == PROVIDER_OPENAI:
        api_key = entry.data.get(CONF_OPENAI_API_KEY)
        if not api_key:
            LOGGER.error("Missing OpenAI API key in config entry")
            return False

        base_url = str(entry.data.get(CONF_OPENAI_BASE_URL, DEFAULT_OPENAI_BASE_URL))
        # Normalize: strip trailing slash, ensure v1 suffix.
        base_url = base_url.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"

        client = OpenAIClient(
            api_key=str(api_key),
            base_url=base_url,
            http_client=get_async_client(hass),
        )

        try:
            await client.async_validate()
        except httpx.HTTPStatusError as err:
            if err.response.status_code in (401, 403):
                LOGGER.error("Invalid OpenAI API key: %s", err)
                return False
            raise ConfigEntryNotReady(err) from err
        except httpx.HTTPError as err:
            raise ConfigEntryNotReady(err) from err

        entry.runtime_data = client
    elif provider == PROVIDER_OPENAI_CODEX:
        access_token = entry.data.get(CONF_ACCESS_TOKEN)
        refresh_token = entry.data.get(CONF_REFRESH_TOKEN)
        expires_at = entry.data.get(CONF_EXPIRES_AT, 0)
        account_id = entry.data.get(CONF_OPENAI_CODEX_ACCOUNT_ID)

        if not access_token or not refresh_token:
            LOGGER.error("Missing OpenAI Codex OAuth tokens in config entry")
            return False

        if not account_id:
            account_id = _extract_openai_codex_account_id(str(access_token))
            if not account_id:
                LOGGER.error("Missing OpenAI Codex account id in config entry")
                return False

        # Refresh token if expired or close to expiry (within 10 minutes)
        if time.time() > (float(expires_at) - 600):
            LOGGER.debug("OpenAI Codex access token expired or near expiry, refreshing...")
            access_token = await _async_refresh_openai_codex_token(hass, entry)
            if not access_token:
                raise ConfigEntryNotReady("Failed to refresh OpenAI Codex OAuth token")
            account_id = entry.data.get(CONF_OPENAI_CODEX_ACCOUNT_ID)

        base_url = str(entry.data.get("base_url", DEFAULT_OPENAI_CODEX_BASE_URL)).rstrip("/")
        client = OpenAICodexClient(
            access_token=str(access_token),
            account_id=str(account_id),
            base_url=base_url,
            http_client=get_async_client(hass),
        )

        try:
            await client.async_validate()
        except httpx.HTTPStatusError as err:
            if err.response.status_code in (401, 403):
                LOGGER.error("Invalid OpenAI Codex OAuth token: %s", err)
                return False
            raise ConfigEntryNotReady(err) from err
        except httpx.HTTPError as err:
            raise ConfigEntryNotReady(err) from err

        entry.runtime_data = client
    elif provider == PROVIDER_GOOGLE_GEMINI_CLI:
        access_token = entry.data.get(CONF_ACCESS_TOKEN)
        refresh_token = entry.data.get(CONF_REFRESH_TOKEN)
        expires_at = entry.data.get(CONF_EXPIRES_AT, 0)
        project_id = entry.data.get(CONF_GOOGLE_PROJECT_ID)

        if not access_token or not refresh_token or not project_id:
            LOGGER.error("Missing Gemini CLI OAuth credentials in config entry")
            return False

        # Refresh token if expired or close to expiry (within 10 minutes)
        if time.time() > (float(expires_at) - 600):
            LOGGER.debug("Gemini CLI access token expired or near expiry, refreshing...")
            access_token = await _async_refresh_gemini_cli_token(hass, entry)
            if not access_token:
                raise ConfigEntryNotReady("Failed to refresh Gemini CLI OAuth token")

        base_url = str(entry.data.get("base_url", DEFAULT_GEMINI_CLI_BASE_URL)).rstrip("/")
        client = GeminiCliClient(
            access_token=str(access_token),
            project_id=str(project_id),
            base_url=base_url,
            http_client=get_async_client(hass),
        )

        try:
            await client.async_validate()
        except httpx.HTTPStatusError as err:
            if err.response.status_code in (401, 403):
                LOGGER.error("Invalid Gemini CLI OAuth token: %s", err)
                return False
            raise ConfigEntryNotReady(err) from err
        except httpx.HTTPError as err:
            raise ConfigEntryNotReady(err) from err

        entry.runtime_data = client
    else:
        access_token = entry.data.get(CONF_ACCESS_TOKEN)
        expires_at = entry.data.get(CONF_EXPIRES_AT, 0)

        # Refresh token if expired or close to expiry (within 10 minutes)
        if time.time() > (expires_at - 600):
            LOGGER.debug("Access token expired or near expiry, refreshing...")
            access_token = await _async_refresh_token(hass, entry)
            if not access_token:
                raise ConfigEntryNotReady("Failed to refresh OAuth token")

        client = _create_client(hass, access_token)

        # Validate the token works
        try:
            await _async_validate_oauth_client(client)
        except anthropic.AuthenticationError as err:
            LOGGER.error("Invalid OAuth token: %s", err)
            # Try refreshing once more
            access_token = await _async_refresh_token(hass, entry)
            if not access_token:
                return False
            client = _create_client(hass, access_token)
            try:
                await _async_validate_oauth_client(client)
            except anthropic.AuthenticationError as err2:
                LOGGER.error("Token refresh did not resolve auth error: %s", err2)
                return False
        except anthropic.AnthropicError as err:
            raise ConfigEntryNotReady(err) from err

        entry.runtime_data = client

    # Migrate old LLM API id (ai_subscription_assist) to per-subentry ids (ai_subscription_assist.<subentry_id>)
    for subentry in entry.subentries.values():
        if subentry.subentry_type != "conversation":
            continue
        llm_apis = subentry.data.get(CONF_LLM_HASS_API)
        if llm_apis == ["ai_subscription_assist"] or llm_apis == "ai_subscription_assist":
            hass.config_entries.async_update_subentry(
                entry,
                subentry,
                data={
                    **subentry.data,
                    CONF_LLM_HASS_API: [f"ai_subscription_assist.{subentry.subentry_id}"],
                },
            )

    # Default to this subentry's dedicated API if nothing is configured yet.
    # For new subentries, the correct API id is only known after HA assigns it.
    for subentry in entry.subentries.values():
        if subentry.subentry_type != "conversation":
            continue
        if subentry.data.get(CONF_LLM_HASS_API) is None:
            hass.config_entries.async_update_subentry(
                entry,
                subentry,
                data={
                    **subentry.data,
                    CONF_LLM_HASS_API: [f"ai_subscription_assist.{subentry.subentry_id}"],
                },
            )

    if provider == PROVIDER_CLAUDE_OAUTH:
        # Set up periodic token refresh (Claude subscription OAuth only).
        async def _periodic_refresh(_now: datetime.datetime) -> None:
            """Periodically refresh the OAuth token."""
            new_token = await _async_refresh_token(hass, entry)
            if new_token:
                entry.runtime_data = _create_client(hass, new_token)

        entry.async_on_unload(
            async_track_time_interval(
                hass,
                _periodic_refresh,
                datetime.timedelta(seconds=TOKEN_REFRESH_INTERVAL),
            )
        )
    elif provider == PROVIDER_OPENAI_CODEX:
        # Check regularly and refresh the access token shortly before expiry.
        async def _periodic_refresh_openai_codex(_now: datetime.datetime) -> None:
            expires_at = float(entry.data.get(CONF_EXPIRES_AT, 0))
            if time.time() <= (expires_at - 600):
                return

            new_token = await _async_refresh_openai_codex_token(hass, entry)
            if not new_token:
                return
            account_id = entry.data.get(CONF_OPENAI_CODEX_ACCOUNT_ID)
            if not account_id:
                return
            base_url = str(entry.data.get("base_url", DEFAULT_OPENAI_CODEX_BASE_URL)).rstrip("/")
            entry.runtime_data = OpenAICodexClient(
                access_token=str(new_token),
                account_id=str(account_id),
                base_url=base_url,
                http_client=get_async_client(hass),
            )

        entry.async_on_unload(
            async_track_time_interval(
                hass,
                _periodic_refresh_openai_codex,
                datetime.timedelta(minutes=10),
            )
        )
    elif provider == PROVIDER_GOOGLE_GEMINI_CLI:
        async def _periodic_refresh_gemini_cli(_now: datetime.datetime) -> None:
            expires_at = float(entry.data.get(CONF_EXPIRES_AT, 0))
            if time.time() <= (expires_at - 600):
                return

            new_token = await _async_refresh_gemini_cli_token(hass, entry)
            if not new_token:
                return
            project_id = entry.data.get(CONF_GOOGLE_PROJECT_ID)
            if not project_id:
                return
            base_url = str(entry.data.get("base_url", DEFAULT_GEMINI_CLI_BASE_URL)).rstrip("/")
            entry.runtime_data = GeminiCliClient(
                access_token=str(new_token),
                project_id=str(project_id),
                base_url=base_url,
                http_client=get_async_client(hass),
            )

        entry.async_on_unload(
            async_track_time_interval(
                hass,
                _periodic_refresh_gemini_cli,
                datetime.timedelta(minutes=10),
            )
        )

    await async_setup_memory_service_for_entry(hass, entry)

    # Register per-agent LLM APIs (one per conversation subentry)
    from .api import async_register_ai_subscription_assist_apis

    unregister_apis = async_register_ai_subscription_assist_apis(hass, entry)
    entry.async_on_unload(unregister_apis)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload AI Subscription Assist."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    async_remove_memory_service_for_entry(hass, entry.entry_id)
    return unloaded


async def async_update_options(
    hass: HomeAssistant, entry: AiSubscriptionAssistConfigEntry
) -> None:
    """Update options."""
    await hass.config_entries.async_reload(entry.entry_id)
