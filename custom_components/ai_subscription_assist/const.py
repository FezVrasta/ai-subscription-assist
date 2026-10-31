"""Constants for the AI Subscription Assist integration."""

import logging

DOMAIN = "ai_subscription_assist"
LOGGER = logging.getLogger(__package__)

DEFAULT_CONVERSATION_NAME = "AI Subscription Assist conversation"
DEFAULT_AI_TASK_NAME = "AI Subscription Assist AI Task"

# Provider types (config entry level)
CONF_PROVIDER = "provider"
PROVIDER_CLAUDE_OAUTH = "claude_oauth"
PROVIDER_OPENAI = "openai"
PROVIDER_OPENAI_CODEX = "openai_codex"
PROVIDER_GOOGLE_GEMINI_CLI = "google_gemini_cli"

# OAuth constants
OAUTH_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
OAUTH_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
OAUTH_SCOPES = "org:create_api_key user:profile user:inference"
OAUTH_REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"

# Beta headers required for OAuth Bearer token auth
OAUTH_BETA_FLAGS = "claude-code-20250219,oauth-2025-04-20"

CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_EXPIRES_AT = "expires_at"

# OpenAI (and OpenAI-compatible) configuration
CONF_OPENAI_API_KEY = "openai_api_key"
CONF_OPENAI_BASE_URL = "openai_base_url"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"

# OpenAI Codex (ChatGPT OAuth subscription)
OPENAI_CODEX_OAUTH_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
OPENAI_CODEX_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENAI_CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_CODEX_OAUTH_SCOPES = "openid profile email offline_access"
OPENAI_CODEX_OAUTH_REDIRECT_URI = "http://localhost:1455/auth/callback"
DEFAULT_OPENAI_CODEX_BASE_URL = "https://chatgpt.com/backend-api"
CONF_OPENAI_CODEX_ACCOUNT_ID = "openai_codex_account_id"

# Google Gemini CLI (Google Cloud Code Assist OAuth)
GOOGLE_GEMINI_CLI_OAUTH_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_GEMINI_CLI_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_GEMINI_CLI_OAUTH_CLIENT_ID = (
    "681255809395-oo8ft2oprd"
    "rnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
)
GOOGLE_GEMINI_CLI_OAUTH_CLIENT_SECRET = "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"
GOOGLE_GEMINI_CLI_OAUTH_REDIRECT_URI = "http://localhost:8085/oauth2callback"
GOOGLE_GEMINI_CLI_OAUTH_SCOPES = (
    "https://www.googleapis.com/auth/cloud-platform "
    "https://www.googleapis.com/auth/userinfo.email "
    "https://www.googleapis.com/auth/userinfo.profile"
)
DEFAULT_GEMINI_CLI_BASE_URL = "https://cloudcode-pa.googleapis.com"
CONF_GOOGLE_PROJECT_ID = "google_project_id"
CONF_GOOGLE_USER_EMAIL = "google_user_email"

CONF_RECOMMENDED = "recommended"
CONF_PROMPT = "prompt"
CONF_CHAT_MODEL = "chat_model"
CONF_MAX_TOKENS = "max_tokens"
CONF_TEMPERATURE = "temperature"
CONF_THINKING_BUDGET = "thinking_budget"
CONF_THINKING_EFFORT = "thinking_effort"
CONF_WEB_SEARCH = "web_search"
CONF_WEB_SEARCH_USER_LOCATION = "user_location"
CONF_WEB_SEARCH_MAX_USES = "web_search_max_uses"
CONF_WEB_SEARCH_CITY = "city"
CONF_WEB_SEARCH_REGION = "region"
CONF_WEB_SEARCH_COUNTRY = "country"
CONF_WEB_SEARCH_TIMEZONE = "timezone"

CONF_ENABLED_TOOLS = "enabled_tools"
CONF_YOLO_MODE = "yolo_mode"

# Service-level memory options (config entry options)
CONF_MEMORY_ENABLED = "memory_enabled"
CONF_MEMORY_AUTO_WRITE = "memory_auto_write"
CONF_MEMORY_AUTO_RECALL = "memory_auto_recall"
CONF_MEMORY_TTL_DAYS = "memory_ttl_days"
CONF_MEMORY_MAX_ITEMS_PER_SCOPE = "memory_max_items_per_scope"
CONF_MEMORY_RECALL_TOP_K = "memory_recall_top_k"
CONF_MEMORY_RESUME_ENABLED = "memory_resume_enabled"
CONF_MEMORY_RESUME_MAX_MESSAGES = "memory_resume_max_messages"

MEMORY_DEFAULTS = {
    CONF_MEMORY_ENABLED: False,
    CONF_MEMORY_AUTO_WRITE: True,
    CONF_MEMORY_AUTO_RECALL: True,
    CONF_MEMORY_TTL_DAYS: 180,
    CONF_MEMORY_MAX_ITEMS_PER_SCOPE: 300,
    CONF_MEMORY_RECALL_TOP_K: 5,
    CONF_MEMORY_RESUME_ENABLED: True,
    CONF_MEMORY_RESUME_MAX_MESSAGES: 40,
}

DATA_REPAIR_DEFER_RELOAD = "repair_defer_reload"
DATA_MEMORY_SERVICES = "memory_services"
DATA_MEMORY_WS_REGISTERED = "memory_ws_registered"
DATA_MEMORY_PANEL_REGISTERED = "memory_panel_registered"

PANEL_URL_PATH = "ai-subscription-assist-memory"
PANEL_COMPONENT_NAME = "ai-subscription-assist-memory-panel"
PANEL_SIDEBAR_TITLE = "AI Assist Memory"
PANEL_SIDEBAR_ICON = "mdi:brain"
PANEL_STATIC_BASE_URL = f"/api/{DOMAIN}/panel"
PANEL_MODULE_URL = f"{PANEL_STATIC_BASE_URL}/ai-subscription-assist-memory-panel.js"

DEFAULT = {
    CONF_CHAT_MODEL: "claude-haiku-4-5",
    CONF_MAX_TOKENS: 3000,
    CONF_TEMPERATURE: 1.0,
    CONF_THINKING_BUDGET: 0,
    CONF_THINKING_EFFORT: "low",
    CONF_WEB_SEARCH: False,
    CONF_WEB_SEARCH_USER_LOCATION: False,
    CONF_WEB_SEARCH_MAX_USES: 5,
    CONF_YOLO_MODE: False,
}

DEFAULT_OPENAI_CHAT_MODEL = "gpt-4o-mini"
DEFAULT_OPENAI_CODEX_MODEL = "gpt-5.2-codex"
DEFAULT_GEMINI_CLI_MODEL = "gemini-2.5-flash"

MIN_THINKING_BUDGET = 1024

NON_THINKING_MODELS = [
    "claude-3-5",  # Both sonnet and haiku
    "claude-3-opus",
    "claude-3-haiku",
]

NON_ADAPTIVE_THINKING_MODELS = [
    "claude-opus-4-5",
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
    "claude-opus-4-1",
    "claude-opus-4-0",
    "claude-opus-4-20250514",
    "claude-sonnet-4-0",
    "claude-sonnet-4-20250514",
    "claude-3",
]

WEB_SEARCH_UNSUPPORTED_MODELS = [
    "claude-3-haiku",
    "claude-3-opus",
    "claude-3-5-sonnet-20240620",
    "claude-3-5-sonnet-20241022",
]

DEPRECATED_MODELS = [
    "claude-3-5-haiku",
    "claude-3-7-sonnet",
    "claude-3-5-sonnet",
    "claude-3-opus",
]

# How often to check whether the Claude OAuth token is close to expiry (tokens last 8 hours)
TOKEN_REFRESH_INTERVAL = 5 * 60
