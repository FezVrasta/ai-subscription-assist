"""Config flow for AI Subscription Assist integration."""

from __future__ import annotations

import base64
import asyncio
import hashlib
import json
import logging
import re
import secrets
import time
from typing import Any, cast
from urllib.parse import urlencode

import anthropic
import httpx
import voluptuous as vol

from homeassistant.components.zone import ENTITY_ID_HOME
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import (
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    CONF_LLM_HASS_API,
    CONF_NAME,
)
from homeassistant.core import callback
from homeassistant.helpers import llm
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TemplateSelector,
)
from homeassistant.helpers.typing import VolDictType

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_CHAT_MODEL,
    CONF_ENABLED_TOOLS,
    CONF_EXPIRES_AT,
    CONF_GOOGLE_PROJECT_ID,
    CONF_GOOGLE_USER_EMAIL,
    CONF_MAX_TOKENS,
    CONF_MEMORY_AUTO_RECALL,
    CONF_MEMORY_AUTO_WRITE,
    CONF_MEMORY_ENABLED,
    CONF_MEMORY_MAX_ITEMS_PER_SCOPE,
    CONF_MEMORY_RECALL_TOP_K,
    CONF_MEMORY_RESUME_ENABLED,
    CONF_MEMORY_RESUME_MAX_MESSAGES,
    CONF_MEMORY_TTL_DAYS,
    CONF_OPENAI_API_KEY,
    CONF_OPENAI_BASE_URL,
    CONF_OPENAI_CODEX_ACCOUNT_ID,
    CONF_PROMPT,
    CONF_PROVIDER,
    CONF_RECOMMENDED,
    CONF_REFRESH_TOKEN,
    CONF_TEMPERATURE,
    CONF_THINKING_BUDGET,
    CONF_THINKING_EFFORT,
    CONF_WEB_SEARCH,
    CONF_WEB_SEARCH_CITY,
    CONF_WEB_SEARCH_COUNTRY,
    CONF_WEB_SEARCH_MAX_USES,
    CONF_WEB_SEARCH_REGION,
    CONF_WEB_SEARCH_TIMEZONE,
    CONF_WEB_SEARCH_USER_LOCATION,
    CONF_YOLO_MODE,
    DEFAULT,
    DEFAULT_CONVERSATION_NAME,
    DEFAULT_GEMINI_CLI_BASE_URL,
    DEFAULT_GEMINI_CLI_MODEL,
    DEFAULT_OPENAI_BASE_URL,
    DEFAULT_OPENAI_CHAT_MODEL,
    DEFAULT_OPENAI_CODEX_MODEL,
    DOMAIN,
    GOOGLE_GEMINI_CLI_OAUTH_AUTHORIZE_URL,
    GOOGLE_GEMINI_CLI_OAUTH_CLIENT_ID,
    GOOGLE_GEMINI_CLI_OAUTH_CLIENT_SECRET,
    GOOGLE_GEMINI_CLI_OAUTH_REDIRECT_URI,
    GOOGLE_GEMINI_CLI_OAUTH_SCOPES,
    GOOGLE_GEMINI_CLI_OAUTH_TOKEN_URL,
    NON_ADAPTIVE_THINKING_MODELS,
    NON_THINKING_MODELS,
    OAUTH_AUTHORIZE_URL,
    OAUTH_BETA_FLAGS,
    OAUTH_CLIENT_ID,
    OAUTH_REDIRECT_URI,
    OAUTH_SCOPES,
    OAUTH_TOKEN_URL,
    OPENAI_CODEX_OAUTH_AUTHORIZE_URL,
    OPENAI_CODEX_OAUTH_CLIENT_ID,
    OPENAI_CODEX_OAUTH_REDIRECT_URI,
    OPENAI_CODEX_OAUTH_SCOPES,
    OPENAI_CODEX_OAUTH_TOKEN_URL,
    PROVIDER_CLAUDE_OAUTH,
    PROVIDER_OPENAI,
    PROVIDER_OPENAI_CODEX,
    PROVIDER_GOOGLE_GEMINI_CLI,
    WEB_SEARCH_UNSUPPORTED_MODELS,
    MEMORY_DEFAULTS,
)

from .schema_compat import to_openapi
from .tools import (
    get_custom_tool_options,
    get_default_enabled_tools,
    normalize_enabled_tools,
)

_LOGGER = logging.getLogger(__name__)


DEFAULT_CONVERSATION_OPTIONS = {
    CONF_RECOMMENDED: True,
    CONF_PROMPT: llm.DEFAULT_INSTRUCTIONS_PROMPT,
    CONF_YOLO_MODE: False,
}


def _default_conversation_options(provider: str) -> dict[str, Any]:
    """Return default conversation subentry options for a provider."""
    opts = DEFAULT_CONVERSATION_OPTIONS.copy()
    if provider == PROVIDER_OPENAI:
        opts[CONF_CHAT_MODEL] = DEFAULT_OPENAI_CHAT_MODEL
    elif provider == PROVIDER_OPENAI_CODEX:
        opts[CONF_CHAT_MODEL] = DEFAULT_OPENAI_CODEX_MODEL
    elif provider == PROVIDER_GOOGLE_GEMINI_CLI:
        opts[CONF_CHAT_MODEL] = DEFAULT_GEMINI_CLI_MODEL
    else:
        opts[CONF_CHAT_MODEL] = DEFAULT[CONF_CHAT_MODEL]
    return opts


def _default_conversation_title(provider: str) -> str:
    """Return the default subentry title for a provider."""
    if provider == PROVIDER_CLAUDE_OAUTH:
        return "Claude conversation"
    if provider == PROVIDER_OPENAI:
        return "OpenAI API conversation"
    if provider == PROVIDER_OPENAI_CODEX:
        return "ChatGPT Plus/Pro conversation"
    if provider == PROVIDER_GOOGLE_GEMINI_CLI:
        return "Google AI Pro conversation"
    return DEFAULT_CONVERSATION_NAME


def _normalize_openai_base_url(base_url: str) -> str:
    base_url = base_url.strip().rstrip("/")
    if not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"
    return base_url


def _generate_pkce_pair() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge."""
    code_verifier = secrets.token_urlsafe(64)[:128]
    code_challenge_digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = (
        base64.urlsafe_b64encode(code_challenge_digest).rstrip(b"=").decode("ascii")
    )
    return code_verifier, code_challenge


def _generate_state() -> str:
    """Generate a CSRF state value."""
    return secrets.token_urlsafe(32)


def _parse_oauth_redirect_input(raw_input: str) -> tuple[str | None, str | None]:
    """Parse a pasted OAuth redirect URL or shorthand into (code, state)."""
    value = raw_input.strip()
    if not value:
        return None, None

    # Full URL paste (preferred)
    try:
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(value)
        if parsed.scheme and parsed.netloc:
            qs = parse_qs(parsed.query)
            code = qs.get("code", [None])[0]
            state = qs.get("state", [None])[0]
            return (
                str(code) if code else None,
                str(state) if state else None,
            )
    except Exception:
        pass

    # "code#state" paste (some providers show this format)
    if "#" in value:
        code, state = value.split("#", 1)
        return code or None, state or None

    # Raw code only.
    return value, None


def _extract_openai_codex_account_id(access_token: str) -> str | None:
    """Extract ChatGPT account id from an OpenAI OAuth JWT."""
    try:
        parts = access_token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(payload_b64.encode("ascii")).decode("utf-8")
        )
        if not isinstance(payload, dict):
            return None
        auth = payload.get("https://api.openai.com/auth")
        if not isinstance(auth, dict):
            return None
        account_id = auth.get("chatgpt_account_id")
        if isinstance(account_id, str) and account_id:
            return account_id
    except Exception:
        return None
    return None


def _is_gemini_cli_vpc_sc_affected(payload: Any) -> bool:
    """Detect VPC SC affected users from a Google RPC error payload."""
    if not payload or not isinstance(payload, dict):
        return False
    err = payload.get("error")
    if not isinstance(err, dict):
        return False
    details = err.get("details")
    if not isinstance(details, list):
        return False
    return any(
        isinstance(d, dict) and d.get("reason") == "SECURITY_POLICY_VIOLATED"
        for d in details
    )


def _gemini_cli_default_tier_id(allowed_tiers: Any) -> str:
    """Choose a default tier id from loadCodeAssist payload."""
    if not isinstance(allowed_tiers, list) or not allowed_tiers:
        return "legacy-tier"
    for tier in allowed_tiers:
        if isinstance(tier, dict) and tier.get("isDefault"):
            tid = tier.get("id")
            return str(tid) if tid else "legacy-tier"
    tid = allowed_tiers[0].get("id") if isinstance(allowed_tiers[0], dict) else None
    return str(tid) if tid else "legacy-tier"


async def _async_gemini_cli_get_user_email(hass, access_token: str) -> str | None:
    """Fetch the user email (best effort)."""
    try:
        async_client = get_async_client(hass)
        resp = await async_client.get(
            "https://www.googleapis.com/oauth2/v1/userinfo?alt=json",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        email = data.get("email")
        return str(email) if isinstance(email, str) and email else None
    except Exception:
        return None


async def _async_gemini_cli_discover_project(
    hass,
    access_token: str,
    project_hint: str | None,
) -> str:
    """Discover or provision a Cloud Code Assist project for the user."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        # Mimic Gemini CLI / Google client expectations.
        "User-Agent": "google-api-python-client",
        "X-Goog-Api-Client": "gl-python/3.13",
    }

    async_client = get_async_client(hass)
    load_payload = {
        "cloudaicompanionProject": project_hint,
        "metadata": {
            "ideType": "IDE_UNSPECIFIED",
            "platform": "PLATFORM_UNSPECIFIED",
            "pluginType": "GEMINI",
            "duetProject": project_hint,
        },
    }

    load_resp = await async_client.post(
        f"{DEFAULT_GEMINI_CLI_BASE_URL}/v1internal:loadCodeAssist",
        headers=headers,
        json=load_payload,
    )

    if load_resp.status_code != 200:
        # Some accounts are VPC SC affected; treat them as standard tier.
        try:
            payload = load_resp.json()
        except Exception:
            payload = None
        if _is_gemini_cli_vpc_sc_affected(payload):
            data: dict[str, Any] = {"currentTier": {"id": "standard-tier"}}
        else:
            load_resp.raise_for_status()
    else:
        data = cast(dict[str, Any], load_resp.json())

    current_tier = data.get("currentTier")
    if current_tier:
        existing_project = data.get("cloudaicompanionProject")
        if isinstance(existing_project, str) and existing_project:
            return existing_project
        if project_hint:
            return project_hint
        raise ValueError("project_required")

    tier_id = _gemini_cli_default_tier_id(data.get("allowedTiers"))
    if tier_id != "free-tier" and not project_hint:
        raise ValueError("project_required")

    onboard_body: dict[str, Any] = {
        "tierId": tier_id,
        "metadata": {
            "ideType": "IDE_UNSPECIFIED",
            "platform": "PLATFORM_UNSPECIFIED",
            "pluginType": "GEMINI",
        },
    }
    if tier_id != "free-tier" and project_hint:
        onboard_body["cloudaicompanionProject"] = project_hint
        onboard_body["metadata"]["duetProject"] = project_hint

    onboard_resp = await async_client.post(
        f"{DEFAULT_GEMINI_CLI_BASE_URL}/v1internal:onboardUser",
        headers=headers,
        json=onboard_body,
    )
    onboard_resp.raise_for_status()
    op = cast(dict[str, Any], onboard_resp.json())

    # Poll long-running operation until done.
    while not op.get("done") and op.get("name"):
        await asyncio.sleep(5)
        poll_resp = await async_client.get(
            f"{DEFAULT_GEMINI_CLI_BASE_URL}/v1internal/{op['name']}",
            headers=headers,
        )
        poll_resp.raise_for_status()
        op = cast(dict[str, Any], poll_resp.json())

    project_id: str | None = None
    response_payload = op.get("response")
    if isinstance(response_payload, dict):
        project_payload = response_payload.get("cloudaicompanionProject")
        if isinstance(project_payload, dict):
            pid = project_payload.get("id")
            if isinstance(pid, str):
                project_id = pid
    if isinstance(project_id, str) and project_id:
        return project_id

    if project_hint:
        return project_hint

    raise ValueError("project_required")


async def get_model_list(client: anthropic.AsyncAnthropic) -> list[SelectOptionDict]:
    """Get list of available models."""
    try:
        models = (await client.models.list()).data
    except (anthropic.AnthropicError, Exception):
        models = []
    _LOGGER.debug("Available models: %s", models)
    model_options: list[SelectOptionDict] = []
    short_form = re.compile(r"[^\d]-\d$")

    def _major_version(model_id: str) -> int:
        # Common ids: claude-3-5-sonnet-..., claude-haiku-4-5, claude-opus-4-5-20250514
        if "-4" in model_id:
            return 4
        if "-3" in model_id:
            return 3
        return 0

    for model_info in models:
        # Resolve alias from versioned model name
        model_alias = (
            model_info.id[:-9]
            if model_info.id
            not in (
                "claude-3-haiku-20240307",
                "claude-3-5-haiku-20241022",
                "claude-3-opus-20240229",
            )
            and model_info.id[-2:-1] != "-"
            else model_info.id
        )
        if short_form.search(model_alias):
            model_alias += "-0"
        if model_alias.endswith(("haiku", "opus", "sonnet")):
            model_alias += "-latest"

        major = _major_version(model_alias)
        legacy = 0 < major < 4
        label = model_info.display_name
        if legacy:
            label = f"Legacy · {label}"

        model_options.append(
            SelectOptionDict(
                label=label,
                value=model_alias,
            )
        )

    # Order: 4.x first, then legacy (<4)
    model_options.sort(
        key=lambda opt: (
            0 if _major_version(opt["value"]) >= 4 else 1,
            -_major_version(opt["value"]),
            opt["label"].lower(),
        )
    )

    return model_options


class AiSubscriptionAssistConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for AI Subscription Assist."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._code_verifier: str | None = None
        self._state: str | None = None
        self._provider: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which provider backend to configure.

        Use an explicit menu instead of a selector default to avoid accidental
        fallback to Claude when frontend selector payloads are missing/invalid.
        """
        self._provider = None
        self._code_verifier = None
        self._state = None

        return self.async_show_menu(
            step_id="user",
            menu_options=[
                "claude_oauth",
                "openai_codex",
                "gemini_cli",
                "openai",
            ],
        )

    async def async_step_claude_oauth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure Claude OAuth subscription provider."""
        self._provider = PROVIDER_CLAUDE_OAUTH
        errors: dict[str, str] = {}

        if user_input is not None:
            if not self._code_verifier:
                errors["base"] = "flow_expired"
                user_input = None

        if user_input is not None:
            entry_title = str(user_input.get(CONF_NAME) or "Claude")

            # User has submitted the authorization code
            raw_input = str(user_input.get("auth_code", "")).strip()
            # Handle case where user pastes the full redirect URL
            if "code=" in raw_input:
                from urllib.parse import parse_qs, urlparse

                parsed = urlparse(raw_input)
                code_values = parse_qs(parsed.query).get("code", [])
                if code_values:
                    raw_input = code_values[0]

            # The callback page displays "code#state" — split them
            if "#" in raw_input:
                parts = raw_input.split("#", 1)
                auth_code = parts[0]
                callback_state = parts[1]
            else:
                auth_code = raw_input
                callback_state = self._state

            if not auth_code:
                errors["base"] = "no_auth_code"
            else:
                # Exchange the auth code for tokens
                try:
                    async_client = get_async_client(self.hass)
                    response = await async_client.post(
                        OAUTH_TOKEN_URL,
                        json={
                            "grant_type": "authorization_code",
                            "code": auth_code,
                            "state": callback_state,
                            "redirect_uri": OAUTH_REDIRECT_URI,
                            "client_id": OAUTH_CLIENT_ID,
                            "code_verifier": self._code_verifier,
                        },
                        headers={"Content-Type": "application/json"},
                    )
                    response.raise_for_status()
                    token_data = response.json()
                except httpx.HTTPStatusError as err:
                    _LOGGER.error(
                        "Token exchange failed: %s - %s",
                        err.response.status_code,
                        err.response.text,
                    )
                    errors["base"] = "token_exchange_failed"
                except httpx.HTTPError as err:
                    _LOGGER.error("Token exchange HTTP error: %s", err)
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected error during token exchange")
                    errors["base"] = "unknown"

                if not errors:
                    access_token = token_data["access_token"]
                    refresh_token = token_data.get("refresh_token", "")
                    expires_in = token_data.get("expires_in", 28800)
                    expires_at = time.time() + expires_in

                    _LOGGER.info(
                        "OAuth token exchange successful, access token prefix: %s...",
                        access_token[:20] if access_token else "none",
                    )

                    return self.async_create_entry(
                        title=entry_title,
                        data={
                            CONF_PROVIDER: PROVIDER_CLAUDE_OAUTH,
                            CONF_ACCESS_TOKEN: access_token,
                            CONF_REFRESH_TOKEN: refresh_token,
                            CONF_EXPIRES_AT: expires_at,
                        },
                        subentries=[
                            {
                                "subentry_type": "conversation",
                                "data": _default_conversation_options(PROVIDER_CLAUDE_OAUTH),
                                "title": _default_conversation_title(PROVIDER_CLAUDE_OAUTH),
                                "unique_id": None,
                            },
                        ],
                    )

        # Generate PKCE pair once per flow instance.
        if not self._code_verifier or not self._state:
            self._code_verifier, code_challenge = _generate_pkce_pair()
            self._state = self._code_verifier
        else:
            code_challenge_digest = hashlib.sha256(
                self._code_verifier.encode("ascii")
            ).digest()
            code_challenge = (
                base64.urlsafe_b64encode(code_challenge_digest)
                .rstrip(b"=")
                .decode("ascii")
            )

        auth_params = urlencode(
            {
                "code": "true",
                "client_id": OAUTH_CLIENT_ID,
                "response_type": "code",
                "redirect_uri": OAUTH_REDIRECT_URI,
                "scope": OAUTH_SCOPES,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "state": self._state,
            }
        )
        auth_url = f"{OAUTH_AUTHORIZE_URL}?{auth_params}"

        return self.async_show_form(
            step_id="claude_oauth",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_NAME, default="Claude"): str,
                    vol.Required("auth_code"): str,
                }
            ),
            description_placeholders={
                "auth_url": auth_url,
                "redirect_uri": OAUTH_REDIRECT_URI,
            },
            errors=errors or None,
        )

    async def async_step_openai(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure OpenAI (or OpenAI-compatible) provider."""
        self._provider = PROVIDER_OPENAI
        errors: dict[str, str] = {}

        if user_input is not None:
            entry_title = str(user_input.get(CONF_NAME) or "OpenAI")
            api_key = str(user_input.get(CONF_OPENAI_API_KEY, "")).strip()
            base_url = _normalize_openai_base_url(
                str(user_input.get(CONF_OPENAI_BASE_URL, DEFAULT_OPENAI_BASE_URL))
            )

            if not api_key:
                errors["base"] = "authentication_error"
            else:
                try:
                    async_client = get_async_client(self.hass)
                    resp = await async_client.get(
                        f"{base_url}/models",
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
                    resp.raise_for_status()
                except httpx.HTTPStatusError as err:
                    if err.response.status_code in (401, 403):
                        errors["base"] = "authentication_error"
                    else:
                        errors["base"] = "cannot_connect"
                except httpx.HTTPError:
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected error validating OpenAI credentials")
                    errors["base"] = "unknown"

            if not errors:
                return self.async_create_entry(
                    title=entry_title,
                    data={
                        CONF_PROVIDER: PROVIDER_OPENAI,
                        CONF_OPENAI_API_KEY: api_key,
                        CONF_OPENAI_BASE_URL: base_url,
                    },
                    subentries=[
                        {
                            "subentry_type": "conversation",
                            "data": _default_conversation_options(PROVIDER_OPENAI),
                            "title": _default_conversation_title(PROVIDER_OPENAI),
                            "unique_id": None,
                        },
                    ],
                )

        return self.async_show_form(
            step_id="openai",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_NAME, default="OpenAI"): str,
                    vol.Required(CONF_OPENAI_API_KEY): str,
                    vol.Optional(CONF_OPENAI_BASE_URL, default=DEFAULT_OPENAI_BASE_URL): str,
                }
            ),
            errors=errors or None,
        )

    async def async_step_openai_codex(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure ChatGPT Plus/Pro OAuth subscription provider."""
        self._provider = PROVIDER_OPENAI_CODEX
        errors: dict[str, str] = {}

        if user_input is not None:
            if not self._code_verifier or not self._state:
                errors["base"] = "flow_expired"
                user_input = None

        if user_input is not None:
            entry_title = str(user_input.get(CONF_NAME) or "ChatGPT Plus/Pro")

            raw_input = str(user_input.get("auth_code", "")).strip()
            code, callback_state = _parse_oauth_redirect_input(raw_input)
            if not callback_state:
                callback_state = self._state

            if not code:
                errors["base"] = "no_auth_code"
            elif self._state and callback_state and callback_state != self._state:
                errors["base"] = "invalid_state"
            else:
                try:
                    async_client = get_async_client(self.hass)
                    response = await async_client.post(
                        OPENAI_CODEX_OAUTH_TOKEN_URL,
                        data={
                            "grant_type": "authorization_code",
                            "client_id": OPENAI_CODEX_OAUTH_CLIENT_ID,
                            "code": code,
                            "code_verifier": self._code_verifier,
                            "redirect_uri": OPENAI_CODEX_OAUTH_REDIRECT_URI,
                        },
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
                    response.raise_for_status()
                    token_data = response.json()
                except httpx.HTTPStatusError as err:
                    _LOGGER.error(
                        "OpenAI token exchange failed: %s - %s",
                        err.response.status_code,
                        err.response.text,
                    )
                    errors["base"] = "token_exchange_failed"
                except httpx.HTTPError as err:
                    _LOGGER.error("OpenAI token exchange HTTP error: %s", err)
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected error during OpenAI token exchange")
                    errors["base"] = "unknown"

                if not errors:
                    access_token = token_data.get("access_token")
                    refresh_token = token_data.get("refresh_token")
                    expires_in = token_data.get("expires_in")
                    if (
                        not isinstance(access_token, str)
                        or not access_token
                        or not isinstance(refresh_token, str)
                        or not refresh_token
                        or not isinstance(expires_in, (int, float))
                    ):
                        errors["base"] = "token_exchange_failed"
                    else:
                        account_id = _extract_openai_codex_account_id(access_token)
                        if not account_id:
                            errors["base"] = "token_exchange_failed"
                        else:
                            expires_at = time.time() + float(expires_in)
                            return self.async_create_entry(
                                title=entry_title,
                                data={
                                    CONF_PROVIDER: PROVIDER_OPENAI_CODEX,
                                    CONF_ACCESS_TOKEN: access_token,
                                    CONF_REFRESH_TOKEN: refresh_token,
                                    CONF_EXPIRES_AT: expires_at,
                                    CONF_OPENAI_CODEX_ACCOUNT_ID: account_id,
                                },
                                subentries=[
                                    {
                                        "subentry_type": "conversation",
                                        "data": _default_conversation_options(
                                            PROVIDER_OPENAI_CODEX
                                        ),
                                        "title": _default_conversation_title(
                                            PROVIDER_OPENAI_CODEX
                                        ),
                                        "unique_id": None,
                                    },
                                ],
                            )

        # Generate PKCE/state once per flow instance.
        if not self._code_verifier or not self._state:
            self._code_verifier, code_challenge = _generate_pkce_pair()
            self._state = _generate_state()
        else:
            code_challenge_digest = hashlib.sha256(
                self._code_verifier.encode("ascii")
            ).digest()
            code_challenge = (
                base64.urlsafe_b64encode(code_challenge_digest)
                .rstrip(b"=")
                .decode("ascii")
            )

        auth_params = urlencode(
            {
                "response_type": "code",
                "client_id": OPENAI_CODEX_OAUTH_CLIENT_ID,
                "redirect_uri": OPENAI_CODEX_OAUTH_REDIRECT_URI,
                "scope": OPENAI_CODEX_OAUTH_SCOPES,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "state": self._state,
                "id_token_add_organizations": "true",
                "codex_cli_simplified_flow": "true",
                "originator": "pi",
            }
        )
        auth_url = f"{OPENAI_CODEX_OAUTH_AUTHORIZE_URL}?{auth_params}"

        return self.async_show_form(
            step_id="openai_codex",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_NAME, default="ChatGPT Plus/Pro"): str,
                    vol.Required("auth_code"): str,
                }
            ),
            description_placeholders={
                "auth_url": auth_url,
                "redirect_uri": OPENAI_CODEX_OAUTH_REDIRECT_URI,
            },
            errors=errors or None,
        )

    async def async_step_gemini_cli(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure Google AI Pro OAuth subscription provider."""
        self._provider = PROVIDER_GOOGLE_GEMINI_CLI
        errors: dict[str, str] = {}

        if user_input is not None:
            if not self._code_verifier or not self._state:
                errors["base"] = "flow_expired"
                user_input = None

        if user_input is not None:
            entry_title = str(user_input.get(CONF_NAME) or "Google AI Pro")
            project_hint = str(user_input.get(CONF_GOOGLE_PROJECT_ID) or "").strip() or None

            raw_input = str(user_input.get("auth_code", "")).strip()
            code, callback_state = _parse_oauth_redirect_input(raw_input)
            if not callback_state:
                callback_state = self._state

            if not code:
                errors["base"] = "no_auth_code"
            elif self._state and callback_state and callback_state != self._state:
                errors["base"] = "invalid_state"
            else:
                try:
                    async_client = get_async_client(self.hass)
                    token_resp = await async_client.post(
                        GOOGLE_GEMINI_CLI_OAUTH_TOKEN_URL,
                        data={
                            "client_id": GOOGLE_GEMINI_CLI_OAUTH_CLIENT_ID,
                            "client_secret": GOOGLE_GEMINI_CLI_OAUTH_CLIENT_SECRET,
                            "code": code,
                            "grant_type": "authorization_code",
                            "redirect_uri": GOOGLE_GEMINI_CLI_OAUTH_REDIRECT_URI,
                            "code_verifier": self._code_verifier,
                        },
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
                    token_resp.raise_for_status()
                    token_data = token_resp.json()
                except httpx.HTTPStatusError as err:
                    _LOGGER.error(
                        "Google token exchange failed: %s - %s",
                        err.response.status_code,
                        err.response.text,
                    )
                    errors["base"] = "token_exchange_failed"
                except httpx.HTTPError as err:
                    _LOGGER.error("Google token exchange HTTP error: %s", err)
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected error during Google token exchange")
                    errors["base"] = "unknown"

                if not errors:
                    access_token = token_data.get("access_token")
                    refresh_token = token_data.get("refresh_token")
                    expires_in = token_data.get("expires_in")
                    if (
                        not isinstance(access_token, str)
                        or not access_token
                        or not isinstance(refresh_token, str)
                        or not refresh_token
                        or not isinstance(expires_in, (int, float))
                    ):
                        errors["base"] = "token_exchange_failed"
                    else:
                        try:
                            email = await _async_gemini_cli_get_user_email(
                                self.hass, access_token
                            )
                            project_id = await _async_gemini_cli_discover_project(
                                self.hass,
                                access_token,
                                project_hint,
                            )
                        except ValueError as err:
                            if str(err) == "project_required":
                                errors["base"] = "google_project_required"
                            else:
                                errors["base"] = "unknown"
                        except httpx.HTTPError:
                            errors["base"] = "cannot_connect"
                        except Exception:
                            _LOGGER.exception("Unexpected error provisioning project")
                            errors["base"] = "unknown"

                        if not errors:
                            expires_at = time.time() + float(expires_in) - 5 * 60
                            return self.async_create_entry(
                                title=entry_title,
                                data={
                                    CONF_PROVIDER: PROVIDER_GOOGLE_GEMINI_CLI,
                                    CONF_ACCESS_TOKEN: access_token,
                                    CONF_REFRESH_TOKEN: refresh_token,
                                    CONF_EXPIRES_AT: expires_at,
                                    CONF_GOOGLE_PROJECT_ID: project_id,
                                    **(
                                        {CONF_GOOGLE_USER_EMAIL: email}
                                        if email
                                        else {}
                                    ),
                                },
                                subentries=[
                                    {
                                        "subentry_type": "conversation",
                                        "data": _default_conversation_options(
                                            PROVIDER_GOOGLE_GEMINI_CLI
                                        ),
                                        "title": _default_conversation_title(
                                            PROVIDER_GOOGLE_GEMINI_CLI
                                        ),
                                        "unique_id": None,
                                    },
                                ],
                            )

        # Gemini CLI uses state == verifier in practice; keep it stable per flow.
        if not self._code_verifier or not self._state:
            self._code_verifier, code_challenge = _generate_pkce_pair()
            self._state = self._code_verifier
        else:
            code_challenge_digest = hashlib.sha256(
                self._code_verifier.encode("ascii")
            ).digest()
            code_challenge = (
                base64.urlsafe_b64encode(code_challenge_digest)
                .rstrip(b"=")
                .decode("ascii")
            )

        auth_params = urlencode(
            {
                "client_id": GOOGLE_GEMINI_CLI_OAUTH_CLIENT_ID,
                "response_type": "code",
                "redirect_uri": GOOGLE_GEMINI_CLI_OAUTH_REDIRECT_URI,
                "scope": GOOGLE_GEMINI_CLI_OAUTH_SCOPES,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "state": self._state,
                "access_type": "offline",
                "prompt": "consent",
            }
        )
        auth_url = f"{GOOGLE_GEMINI_CLI_OAUTH_AUTHORIZE_URL}?{auth_params}"

        return self.async_show_form(
            step_id="gemini_cli",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_NAME, default="Google AI Pro"): str,
                    vol.Optional(CONF_GOOGLE_PROJECT_ID): str,
                    vol.Required("auth_code"): str,
                }
            ),
            description_placeholders={
                "auth_url": auth_url,
                "redirect_uri": GOOGLE_GEMINI_CLI_OAUTH_REDIRECT_URI,
            },
            errors=errors or None,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return AiSubscriptionAssistOptionsFlow(config_entry)

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentries supported by this integration."""
        return {
            "conversation": ConversationSubentryFlowHandler,
        }


class AiSubscriptionAssistOptionsFlow(OptionsFlow):
    """Service-level options flow for memory settings."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage service-level memory settings."""
        if user_input is not None:
            normalized = {
                CONF_MEMORY_ENABLED: bool(
                    user_input.get(
                        CONF_MEMORY_ENABLED,
                        MEMORY_DEFAULTS[CONF_MEMORY_ENABLED],
                    )
                ),
                CONF_MEMORY_AUTO_WRITE: bool(
                    user_input.get(
                        CONF_MEMORY_AUTO_WRITE,
                        MEMORY_DEFAULTS[CONF_MEMORY_AUTO_WRITE],
                    )
                ),
                CONF_MEMORY_AUTO_RECALL: bool(
                    user_input.get(
                        CONF_MEMORY_AUTO_RECALL,
                        MEMORY_DEFAULTS[CONF_MEMORY_AUTO_RECALL],
                    )
                ),
                CONF_MEMORY_RESUME_ENABLED: bool(
                    user_input.get(
                        CONF_MEMORY_RESUME_ENABLED,
                        MEMORY_DEFAULTS[CONF_MEMORY_RESUME_ENABLED],
                    )
                ),
                CONF_MEMORY_TTL_DAYS: int(
                    user_input.get(
                        CONF_MEMORY_TTL_DAYS,
                        MEMORY_DEFAULTS[CONF_MEMORY_TTL_DAYS],
                    )
                ),
                CONF_MEMORY_MAX_ITEMS_PER_SCOPE: int(
                    user_input.get(
                        CONF_MEMORY_MAX_ITEMS_PER_SCOPE,
                        MEMORY_DEFAULTS[CONF_MEMORY_MAX_ITEMS_PER_SCOPE],
                    )
                ),
                CONF_MEMORY_RECALL_TOP_K: int(
                    user_input.get(
                        CONF_MEMORY_RECALL_TOP_K,
                        MEMORY_DEFAULTS[CONF_MEMORY_RECALL_TOP_K],
                    )
                ),
                CONF_MEMORY_RESUME_MAX_MESSAGES: int(
                    user_input.get(
                        CONF_MEMORY_RESUME_MAX_MESSAGES,
                        MEMORY_DEFAULTS[CONF_MEMORY_RESUME_MAX_MESSAGES],
                    )
                ),
            }
            return self.async_create_entry(title="", data=normalized)

        current = {
            key: self._config_entry.options.get(key, default)
            for key, default in MEMORY_DEFAULTS.items()
        }
        schema: VolDictType = {
            vol.Optional(
                CONF_MEMORY_ENABLED,
                default=current[CONF_MEMORY_ENABLED],
            ): bool,
            vol.Optional(
                CONF_MEMORY_AUTO_WRITE,
                default=current[CONF_MEMORY_AUTO_WRITE],
            ): bool,
            vol.Optional(
                CONF_MEMORY_AUTO_RECALL,
                default=current[CONF_MEMORY_AUTO_RECALL],
            ): bool,
            vol.Optional(
                CONF_MEMORY_RESUME_ENABLED,
                default=current[CONF_MEMORY_RESUME_ENABLED],
            ): bool,
            vol.Optional(
                CONF_MEMORY_TTL_DAYS,
                default=current[CONF_MEMORY_TTL_DAYS],
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=3650)),
            vol.Optional(
                CONF_MEMORY_MAX_ITEMS_PER_SCOPE,
                default=current[CONF_MEMORY_MAX_ITEMS_PER_SCOPE],
            ): vol.All(vol.Coerce(int), vol.Range(min=10, max=5000)),
            vol.Optional(
                CONF_MEMORY_RECALL_TOP_K,
                default=current[CONF_MEMORY_RECALL_TOP_K],
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=20)),
            vol.Optional(
                CONF_MEMORY_RESUME_MAX_MESSAGES,
                default=current[CONF_MEMORY_RESUME_MAX_MESSAGES],
            ): vol.All(vol.Coerce(int), vol.Range(min=4, max=200)),
        }
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))


class ConversationSubentryFlowHandler(ConfigSubentryFlow):
    """Flow for managing conversation subentries."""

    options: dict[str, Any]

    def _get_provider(self) -> str:
        """Return the provider type for the parent config entry."""
        entry = self._get_entry()
        return str(entry.data.get(CONF_PROVIDER, PROVIDER_CLAUDE_OAUTH))

    @property
    def _is_new(self) -> bool:
        """Return if this is a new subentry."""
        return self.source == "user"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a subentry."""
        self.options = _default_conversation_options(self._get_provider())
        return await self.async_step_init()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle reconfiguration of a subentry."""
        self.options = self._get_reconfigure_subentry().data.copy()
        return await self.async_step_init()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Set initial options."""
        # abort if entry is not loaded
        if self._get_entry().state != ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")

        hass_apis: list[SelectOptionDict] = [
            SelectOptionDict(
                label=api.name,
                value=api.id,
            )
            for api in llm.async_get_apis(self.hass)
        ]
        if suggested_llm_apis := self.options.get(CONF_LLM_HASS_API):
            if isinstance(suggested_llm_apis, str):
                suggested_llm_apis = [suggested_llm_apis]
            known_apis = {api.id for api in llm.async_get_apis(self.hass)}
            self.options[CONF_LLM_HASS_API] = [
                api for api in suggested_llm_apis if api in known_apis
            ]

        step_schema: VolDictType = {}
        errors: dict[str, str] = {}

        if self._is_new:
            default_name = _default_conversation_title(self._get_provider())
            step_schema[vol.Required(CONF_NAME, default=default_name)] = str

        tool_options = get_custom_tool_options()
        yolo_mode_default = bool(
            self.options.get(CONF_YOLO_MODE, DEFAULT[CONF_YOLO_MODE])
        )
        enabled_tools_default = normalize_enabled_tools(
            self.options.get(CONF_ENABLED_TOOLS), yolo_mode_default
        )
        if CONF_ENABLED_TOOLS not in self.options:
            enabled_tools_default = get_default_enabled_tools(yolo_mode_default)
        self.options[CONF_YOLO_MODE] = yolo_mode_default
        self.options[CONF_ENABLED_TOOLS] = enabled_tools_default

        step_schema.update(
            {
                vol.Optional(
                    CONF_CHAT_MODEL,
                    default=self.options.get(CONF_CHAT_MODEL, DEFAULT[CONF_CHAT_MODEL]),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=await self._get_model_list(),
                        custom_value=True,
                    )
                ),
                vol.Optional(
                    CONF_ENABLED_TOOLS,
                    default=enabled_tools_default,
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(label=o["label"], value=o["value"])
                            for o in tool_options
                        ],
                        multiple=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_YOLO_MODE,
                    default=yolo_mode_default,
                ): bool,
                vol.Optional(CONF_PROMPT): TemplateSelector(),
                vol.Optional(
                    CONF_LLM_HASS_API,
                ): SelectSelector(
                    SelectSelectorConfig(options=hass_apis, multiple=True)
                ),
            }
        )

        step_schema[
            vol.Required(
                CONF_RECOMMENDED, default=self.options.get(CONF_RECOMMENDED, False)
            )
        ] = bool

        if user_input is not None:
            yolo_mode = bool(user_input.get(CONF_YOLO_MODE, yolo_mode_default))
            user_input[CONF_YOLO_MODE] = yolo_mode
            selected_tools = user_input.get(
                CONF_ENABLED_TOOLS,
                self.options.get(CONF_ENABLED_TOOLS, enabled_tools_default),
            )
            user_input[CONF_ENABLED_TOOLS] = normalize_enabled_tools(
                selected_tools, yolo_mode
            )

            # For new subentries, the correct per-subentry LLM API id does not exist
            # yet. We default it later during config entry setup once we have the
            # generated subentry id.
            llm_apis = user_input.get(CONF_LLM_HASS_API)
            if self._is_new:
                if not llm_apis:
                    user_input.pop(CONF_LLM_HASS_API, None)
            else:
                # For reconfigure, preserve an explicit "no APIs selected" as []
                # so the user can disable HA control for the agent.
                if llm_apis is None:
                    user_input[CONF_LLM_HASS_API] = []

            if user_input[CONF_RECOMMENDED]:
                if not errors:
                    if self._is_new:
                        return self.async_create_entry(
                            title=user_input.pop(CONF_NAME),
                            data=user_input,
                        )
                    return self.async_update_and_abort(
                        self._get_entry(),
                        self._get_reconfigure_subentry(),
                        data=user_input,
                    )
            else:
                self.options.update(user_input)
                if (
                    CONF_LLM_HASS_API in self.options
                    and CONF_LLM_HASS_API not in user_input
                ):
                    self.options.pop(CONF_LLM_HASS_API)
                if not errors:
                    return await self.async_step_advanced()

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(step_schema), self.options
            ),
            errors=errors or None,
        )

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Manage advanced options."""
        errors: dict[str, str] = {}

        step_schema: VolDictType = {
            vol.Optional(
                CONF_CHAT_MODEL,
                default=DEFAULT[CONF_CHAT_MODEL],
            ): SelectSelector(
                SelectSelectorConfig(
                    options=await self._get_model_list(), custom_value=True
                )
            ),
            vol.Optional(
                CONF_MAX_TOKENS,
                default=DEFAULT[CONF_MAX_TOKENS],
            ): int,
            vol.Optional(
                CONF_TEMPERATURE,
                default=DEFAULT[CONF_TEMPERATURE],
            ): NumberSelector(NumberSelectorConfig(min=0, max=1, step=0.05)),
        }

        if user_input is not None:
            self.options.update(user_input)

            if not errors:
                if self._get_provider() != PROVIDER_CLAUDE_OAUTH:
                    # OpenAI (and other non-Claude providers) do not support Claude-
                    # specific model settings like extended thinking and web search.
                    if self._is_new:
                        return self.async_create_entry(
                            title=self.options.pop(CONF_NAME),
                            data=self.options,
                        )
                    return self.async_update_and_abort(
                        self._get_entry(),
                        self._get_reconfigure_subentry(),
                        data=self.options,
                    )

                return await self.async_step_model()

        return self.async_show_form(
            step_id="advanced",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(step_schema), self.options
            ),
            errors=errors,
        )

    async def async_step_model(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Manage model-specific options."""
        errors: dict[str, str] = {}

        if self._get_provider() != PROVIDER_CLAUDE_OAUTH:
            # Safety net: this step should not normally be reached for non-Claude
            # providers. Persist current options and exit.
            if user_input is None:
                user_input = {}
            self.options.update(user_input)

            if self._is_new:
                return self.async_create_entry(
                    title=self.options.pop(CONF_NAME),
                    data=self.options,
                )

            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                data=self.options,
            )

        step_schema: VolDictType = {}

        model = self.options[CONF_CHAT_MODEL]

        if not model.startswith(tuple(NON_THINKING_MODELS)) and model.startswith(
            tuple(NON_ADAPTIVE_THINKING_MODELS)
        ):
            step_schema[
                vol.Optional(
                    CONF_THINKING_BUDGET, default=DEFAULT[CONF_THINKING_BUDGET]
                )
            ] = vol.All(
                NumberSelector(
                    NumberSelectorConfig(
                        min=0,
                        max=self.options.get(CONF_MAX_TOKENS, DEFAULT[CONF_MAX_TOKENS]),
                    )
                ),
                vol.Coerce(int),
            )
        else:
            self.options.pop(CONF_THINKING_BUDGET, None)

        if not model.startswith(tuple(NON_ADAPTIVE_THINKING_MODELS)):
            step_schema[
                vol.Optional(
                    CONF_THINKING_EFFORT,
                    default=DEFAULT[CONF_THINKING_EFFORT],
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=["none", "low", "medium", "high", "max"],
                    translation_key=CONF_THINKING_EFFORT,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        else:
            self.options.pop(CONF_THINKING_EFFORT, None)

        if not model.startswith(tuple(WEB_SEARCH_UNSUPPORTED_MODELS)):
            step_schema.update(
                {
                    vol.Optional(
                        CONF_WEB_SEARCH,
                        default=DEFAULT[CONF_WEB_SEARCH],
                    ): bool,
                    vol.Optional(
                        CONF_WEB_SEARCH_MAX_USES,
                        default=DEFAULT[CONF_WEB_SEARCH_MAX_USES],
                    ): int,
                    vol.Optional(
                        CONF_WEB_SEARCH_USER_LOCATION,
                        default=DEFAULT[CONF_WEB_SEARCH_USER_LOCATION],
                    ): bool,
                }
            )
        else:
            self.options.pop(CONF_WEB_SEARCH, None)
            self.options.pop(CONF_WEB_SEARCH_MAX_USES, None)
            self.options.pop(CONF_WEB_SEARCH_USER_LOCATION, None)

        self.options.pop(CONF_WEB_SEARCH_CITY, None)
        self.options.pop(CONF_WEB_SEARCH_REGION, None)
        self.options.pop(CONF_WEB_SEARCH_COUNTRY, None)
        self.options.pop(CONF_WEB_SEARCH_TIMEZONE, None)

        if not step_schema:
            user_input = {}

        if user_input is not None:
            if user_input.get(CONF_WEB_SEARCH, DEFAULT[CONF_WEB_SEARCH]) and not errors:
                if user_input.get(
                    CONF_WEB_SEARCH_USER_LOCATION,
                    DEFAULT[CONF_WEB_SEARCH_USER_LOCATION],
                ):
                    user_input.update(await self._get_location_data())

            self.options.update(user_input)

            if not errors:
                if self._is_new:
                    return self.async_create_entry(
                        title=self.options.pop(CONF_NAME),
                        data=self.options,
                    )

                return self.async_update_and_abort(
                    self._get_entry(),
                    self._get_reconfigure_subentry(),
                    data=self.options,
                )

        return self.async_show_form(
            step_id="model",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(step_schema), self.options
            ),
            errors=errors or None,
            last_step=True,
        )

    async def _get_model_list(self) -> list[SelectOptionDict]:
        """Get list of available models."""
        entry = self._get_entry()
        provider = self._get_provider()

        if provider == PROVIDER_OPENAI_CODEX:
            return [
                SelectOptionDict(label="GPT-5.3 Codex", value="gpt-5.3-codex"),
                SelectOptionDict(label="GPT-5.2 Codex", value="gpt-5.2-codex"),
                SelectOptionDict(label="GPT-5.1 Codex Max", value="gpt-5.1-codex-max"),
                SelectOptionDict(label="GPT-5.1 Codex Mini", value="gpt-5.1-codex-mini"),
                SelectOptionDict(label="GPT-5.3 Codex Spark", value="gpt-5.3-codex-spark"),
                SelectOptionDict(label="GPT-5.2", value="gpt-5.2"),
                SelectOptionDict(label="GPT-5.1", value="gpt-5.1"),
            ]

        if provider == PROVIDER_GOOGLE_GEMINI_CLI:
            return [
                SelectOptionDict(label="Gemini 2.5 Flash", value="gemini-2.5-flash"),
                SelectOptionDict(label="Gemini 2.5 Pro", value="gemini-2.5-pro"),
                SelectOptionDict(label="Gemini 2.0 Flash", value="gemini-2.0-flash"),
            ]

        if provider == PROVIDER_OPENAI:
            api_key = str(entry.data.get(CONF_OPENAI_API_KEY, "")).strip()
            base_url = _normalize_openai_base_url(
                str(entry.data.get(CONF_OPENAI_BASE_URL, DEFAULT_OPENAI_BASE_URL))
            )
            try:
                async_client = get_async_client(self.hass)
                resp = await async_client.get(
                    f"{base_url}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                resp.raise_for_status()
                payload = resp.json()
                model_ids = [
                    m.get("id")
                    for m in payload.get("data", [])
                    if isinstance(m, dict) and m.get("id")
                ]
                model_options = [
                    SelectOptionDict(label=str(mid), value=str(mid))
                    for mid in model_ids
                ]
                model_options.sort(key=lambda opt: opt["label"].lower())
                if model_options:
                    return model_options
            except Exception:
                pass

            # Fallback to a minimal, well-known list.
            return [
                SelectOptionDict(label="gpt-4o-mini", value="gpt-4o-mini"),
                SelectOptionDict(label="gpt-4o", value="gpt-4o"),
                SelectOptionDict(label="gpt-4.1-mini", value="gpt-4.1-mini"),
                SelectOptionDict(label="gpt-4.1", value="gpt-4.1"),
            ]

        try:
            client = anthropic.AsyncAnthropic(
                auth_token=entry.data[CONF_ACCESS_TOKEN],
                http_client=get_async_client(self.hass),
                default_headers={
                    "anthropic-beta": OAUTH_BETA_FLAGS,
                    "user-agent": "claude-cli/2.1.2 (external, cli)",
                    "x-app": "cli",
                },
            )
            models = await get_model_list(client)
            if models:
                return models
        except Exception:
            pass
        # Fallback to static list if API call fails
        return [
            SelectOptionDict(label="Claude Haiku 4.5", value="claude-haiku-4-5"),
            SelectOptionDict(label="Claude Sonnet 4.5", value="claude-sonnet-4-5-20250514"),
            SelectOptionDict(label="Claude Sonnet 4", value="claude-sonnet-4-20250514"),
            SelectOptionDict(label="Claude Opus 4.5", value="claude-opus-4-5-20250514"),
            SelectOptionDict(label="Legacy · Claude Haiku 3.5", value="claude-3-5-haiku-20241022"),
            SelectOptionDict(label="Legacy · Claude Haiku 3", value="claude-3-haiku-20240307"),
            SelectOptionDict(label="Legacy · Claude Opus 3", value="claude-3-opus-20240229"),
        ]

    async def _get_location_data(self) -> dict[str, str]:
        """Get approximate location data of the user."""
        location_data: dict[str, str] = {}
        if self._get_provider() != PROVIDER_CLAUDE_OAUTH:
            # Only Claude's server-side web search supports the location assist flow.
            if self.hass.config.country:
                location_data[CONF_WEB_SEARCH_COUNTRY] = self.hass.config.country
            location_data[CONF_WEB_SEARCH_TIMEZONE] = self.hass.config.time_zone
            return location_data

        zone_home = self.hass.states.get(ENTITY_ID_HOME)
        if zone_home is not None:
            entry = self._get_entry()
            client = anthropic.AsyncAnthropic(
                auth_token=entry.data[CONF_ACCESS_TOKEN],
                http_client=get_async_client(self.hass),
                default_headers={
                    "anthropic-beta": OAUTH_BETA_FLAGS,
                    "user-agent": "claude-cli/2.1.2 (external, cli)",
                    "x-app": "cli",
                },
            )
            location_schema = vol.Schema(
                {
                    vol.Optional(
                        CONF_WEB_SEARCH_CITY,
                        description="Free text input for the city, e.g. `San Francisco`",
                    ): str,
                    vol.Optional(
                        CONF_WEB_SEARCH_REGION,
                        description="Free text input for the region, e.g. `California`",
                    ): str,
                }
            )
            response = await client.messages.create(
                model=cast(str, DEFAULT[CONF_CHAT_MODEL]),
                messages=[
                    {
                        "role": "user",
                        "content": "Where are the following coordinates located: "
                        f"({zone_home.attributes[ATTR_LATITUDE]},"
                        f" {zone_home.attributes[ATTR_LONGITUDE]})? Please respond "
                        "only with a JSON object using the following schema:\n"
                        f"{to_openapi(location_schema)}",
                    },
                    {
                        "role": "assistant",
                        "content": "{",  # hints the model to skip any preamble
                    },
                ],
                max_tokens=cast(int, DEFAULT[CONF_MAX_TOKENS]),
            )
            _LOGGER.debug("Model response: %s", response.content)
            location_data = location_schema(
                json.loads(
                    "{"
                    + "".join(
                        block.text
                        for block in response.content
                        if isinstance(block, anthropic.types.TextBlock)
                    )
                )
                or {}
            )

        if self.hass.config.country:
            location_data[CONF_WEB_SEARCH_COUNTRY] = self.hass.config.country
        location_data[CONF_WEB_SEARCH_TIMEZONE] = self.hass.config.time_zone

        _LOGGER.debug("Location data: %s", location_data)

        return location_data
