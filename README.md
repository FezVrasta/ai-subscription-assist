# ai-subscription-assist

Use subscription-backed LLMs (OAuth) as **Home Assistant Assist conversation agents**:
- **Claude Pro** / **Claude Max** via **OAuth** (no Anthropic API key required)
- **ChatGPT Plus/Pro/Business/Enterprise** (Codex) via **OAuth**
- **Google AI Pro** via **OAuth** (includes Gemini Code Assist and Gemini CLI)
- **OpenAI / compatible** via **API key** (metered)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/toml0006/ai-subscription-assist)](https://github.com/toml0006/ai-subscription-assist/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## What is this?

**ai-subscription-assist** is a Home Assistant custom integration that lets you use multiple LLM providers as conversation agents inside Home Assistant’s Assist pipeline.

For subscription users, it authenticates using OAuth flows compatible with popular CLIs (Claude Code, Codex, Gemini CLI), so you can avoid pay-per-token API billing when your subscription supports it.

---

## Highlights

- **Requires Home Assistant 2026.2.2+** (Python 3.13+)
- **Multi-provider**:
  - Claude Pro/Max OAuth
  - ChatGPT Plus/Pro/Business/Enterprise (Codex OAuth)
  - Google AI Pro (Gemini OAuth)
  - OpenAI / compatible API keys
- **Multiple entries supported**: set up more than one provider/account
- **Subscription OAuth auth** (PKCE) — no API key required for Claude/Codex/Google AI Pro
- **Automatic token refresh** (access tokens expire ~8 hours)
- **Assist pipeline compatible** (works like a normal HA conversation agent)
- **Per-agent model selection** in the UI (one provider per config entry; multiple agents per entry)
- **Extended tools** (optional) beyond standard Assist capabilities:
  - History (`get_history`)
  - Logbook (`get_logbook`)
  - Statistics (`get_statistics`)
  - Template rendering (`render_template`)
  - Internet lookup, read-only (`internet_lookup`)
  - Automations (`list_automations`, `toggle_automation`, `add_automation`)
  - Dashboards (experimental) (`modify_dashboard`)
  - Generic HA service calls (YOLO) (`call_service`)
  - Notifications (`send_notification`)
  - “Who’s home” (`who_is_home`)
  - Shopping/todo (`manage_list`)
  - Calendar (`get_calendar_events`)
- **Per-agent tool allowlist**: choose exactly which *extra* tools each configured agent can use
- **Per-agent YOLO mode**: explicit opt-in to bypass exposure filtering and enable privileged tools
- **Service-level memory (opt-in)**:
  - shared + per-user memory scopes
  - slash commands for memory and session management (`/memory`, `/sessions`, `/new`)
  - Home Assistant addon services for memory/session admin (`ai_subscription_assist.memory_*`, `ai_subscription_assist.session_*`)
  - resumable conversation context across Assist dialog reopen

---

---

## Speech-to-text (optional)

This integration can also provide a **speech-to-text entity**
(`stt.claude_speech_to_text`), so an Assist pipeline can run from microphone to
reply on a Claude subscription alone — no API key, no local Whisper model.

Select it as the pipeline's speech-to-text engine and pair it with a Claude
conversation agent from this integration.

- Only appears on **Claude subscription OAuth** entries. The transcription
  service rejects API-key authentication, so other providers get no STT entity.
- Streams 16 kHz mono PCM, exactly what the Assist pipeline produces, so no
  transcoding happens.
- Sends Assist-exposed entity and area names as recognition hints, which
  noticeably improves accuracy on device names.
- **29 languages**: the 20 Anthropic documents for Claude Code dictation, plus
  `ar`, `ca`, `fi`, `he`, `hu`, `ro`, `th`, `vi`, `zh`, each checked against
  real audio.

> **How this works.** The transcription endpoint is the one the Claude Code CLI
> uses for `/voice` dictation. It is not part of Anthropic's public API and is
> not documented, and Anthropic gates it to their own clients, so it may change
> or stop working without notice. There is no officially supported alternative:
> the Claude API accepts no audio input at all. Home Assistant's Whisper and
> Speech-to-Phrase engines pair with this integration's conversation agent
> equally well for anyone who would rather not depend on it.

## How it works

```
Siri / HA Voice / UI chat
        ↓
HA Conversation API / Assist Pipeline
        ↓
ai-subscription-assist (provider entry + per-agent config)
        ↓
Provider API (Claude / Codex / Gemini / OpenAI-compatible)
        ↓
Response spoken/displayed by Home Assistant
```

---

## Getting started

Need a guided walkthrough with screenshot placeholders? See:
- [`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md)

### Install

You can install via HACS (recommended) or manually.

### Option A: HACS (recommended)

1. Home Assistant → **HACS**
2. **⋮ → Custom repositories**
3. Add `toml0006/ai-subscription-assist` as **Integration**
4. Install **AI Subscription Assist**
5. Restart Home Assistant

### Option B: Manual

1. Copy `custom_components/ai_subscription_assist` into your HA config directory:
   - `/config/custom_components/ai_subscription_assist`
2. Restart Home Assistant

Note: the integration domain/folder is currently still `ai_subscription_assist` for backwards compatibility.

### Versioning and updates (HACS + Home Assistant)

Home Assistant reads the integration version from:
- `custom_components/ai_subscription_assist/manifest.json`

HACS update detection is most reliable when each release is tagged and published on GitHub.

To bump versions consistently, run:

```bash
./scripts/set_version.sh 1.3.2
```

Then release:

```bash
git add VERSION custom_components/ai_subscription_assist/manifest.json
git commit -m "Release v1.3.2"
git tag v1.3.2
git push origin main --tags
```

### Set up providers

Repeat this once per provider/account you want (multiple config entries are supported):

1. HA → **Settings → Devices & services → Add integration**
2. Search for **AI Subscription Assist**
3. Choose the provider:
   - **Claude Pro/Max (OAuth subscription)**: follow the OAuth instructions (you’ll be sent to a Claude page)
   - **ChatGPT Plus/Pro (Codex) (OAuth subscription)**: follow the OAuth instructions (you’ll be sent to an OpenAI page)
   - **Google AI Pro (OAuth subscription)**: follow the OAuth instructions (you’ll be sent to a Google page)
   - **OpenAI / compatible (API key)**: enter your API key and optional base URL
4. Finish setup

After setup you’ll have a **conversation agent** sub-entry (and you can create multiple agents/sub-entries if you want different models/tool permissions).

#### OAuth notes (Codex/Gemini/Claude)

- The OAuth redirect is usually to a `http://localhost:...` URL which may not load in your browser. That’s expected.
- Copy/paste the full redirect URL into Home Assistant (or for Codex you can paste just the `code=` value).
- If you hit **OAuth state mismatch**, just restart the config flow and try again.
- For **Google AI Pro**, some accounts require a Google Cloud project ID for Gemini Code Assist. If you see the “project required” error, provide a project ID and re-authenticate.

### Add more agents (different model/tools) under the same provider

1. HA → **Settings → Devices & services**
2. Open the **AI Subscription Assist** entry you want
3. Add a new **conversation agent** sub-entry

Each agent has its own model + tool allowlist/YOLO configuration.

### Configure an agent (model + tools)

For each configured conversation agent/sub-entry:

- **Model**: pick a provider-appropriate model (Claude/OpenAI/Gemini); custom values are allowed
- **Enabled tools**: select which *extra* tools this agent can call
- **LLM API selection (important):**
  - Select **AI Subscription Assist — <entry name> — <agent name>**
  - Do **not** also select the plain **Home Assistant** / **Assist** API in the multi-select, or tools may be namespaced and become harder for the model to call.

---

## Siri Shortcut (optional)

You can build a simple Shortcut:

1. **Dictate Text**
2. **Get Contents of URL**
   - URL: `https://<your-ha>/api/conversation/process`
   - Method: `POST`
   - Headers:
     - `Authorization: Bearer <HA Long-Lived Access Token>`
     - `Content-Type: application/json`
   - Body:
     ```json
     {
       "text": "<dictated text>",
       "language": "en",
       "agent_id": "conversation.<your_agent_entity_id>"
     }
     ```
3. Speak `response.speech.plain.speech`

Tip: you can find the agent entity id in **Settings → Devices & services → Entities** (domain `conversation`).

Note: multi-turn “session” behavior depends on whether your caller reuses `conversation_id`.

---

## FAQ

### Does this use my subscription instead of API billing?
Yes, for the OAuth-backed providers (Claude Pro/Max, ChatGPT Plus/Pro/Business/Enterprise via Codex, Google AI Pro). No API key needed for those.

For **OpenAI / compatible (API key)** it uses normal API billing.

### Is this the same as the official Anthropic integration?
Functionally similar on the HA side (conversation agent), but authentication differs:

- **Official Anthropic integration**: API key (metered)
- **ai-subscription-assist**: subscription OAuth (flat subscription)

### Why can’t Claude answer “when did X change” by default?
Standard Assist tools are focused on live state + control. For history/logbook/statistics you must enable the extra tools for that agent.

### Can I close Assist and continue later?
Yes, when service-level memory is enabled with resume context. Use `/new` (or `/reset`) to clear current context and start fresh.

---

## Documentation

### Tool safety and YOLO mode

By default, extra tools are filtered by Home Assistant’s entity exposure settings and this integration’s tool policy.

If you enable **YOLO mode** on an agent, it:
- bypasses entity exposure filtering
- unlocks privileged tools (service calls, automation/dashboard edits, log access)
- still keeps destructive dashboard structure changes (`add_view` / `remove_view`) behind an explicit intent gate

Enable YOLO mode only for trusted agents.

### Memory and slash commands

Memory is configured at the **service entry** level (Integration → Configure), not per agent.

Integration panel (recommended for memory/session management):
- Open sidebar item **AI Assist Memory** (path: `/ai-subscription-assist-memory`)
- Features: entry selector, memory/session tables, session transcript viewer, clear/delete actions
- Uses websocket API commands:
  - `ai_subscription_assist/entry_list`
  - `ai_subscription_assist/memory_status`
  - `ai_subscription_assist/memory_list`
  - `ai_subscription_assist/memory_delete`
  - `ai_subscription_assist/memory_clear`
  - `ai_subscription_assist/session_list`
  - `ai_subscription_assist/session_get`
  - `ai_subscription_assist/session_clear`

Commands:
- `/memory status`
- `/memory add [--shared] <text>`
- `/memory list [mine|shared|all] [--limit N]`
- `/memory search <query> [--limit N]`
- `/memory delete <memory_id>`
- `/memory clear mine|shared|all --confirm`
- `/memory sessions [mine|all] [--limit N]` (alias: `/sessions`)
- `/memory sessions show <session_id> [--limit N]`
- `/memory sessions clear <session_id|mine|all> --confirm`
- aliases: `/remember`, `/forget`, `/memories`, `/sessions`
- context reset: `/new` or `/reset`

Addon services (Developer Tools → Actions):
- `ai_subscription_assist.memory_status`
- `ai_subscription_assist.memory_list`
- `ai_subscription_assist.memory_delete`
- `ai_subscription_assist.memory_clear`
- `ai_subscription_assist.session_list`
- `ai_subscription_assist.session_get`
- `ai_subscription_assist.session_clear`

Notes:
- Default writes are per-user memory.
- Shared memory writes/deletes/clear require an admin user.
- Memory avoids obvious secret-looking text and applies retention/cap limits.
- Semantic recall uses local embeddings when available; otherwise it falls back to lexical matching automatically.

## Development notes (auth details)

This integration works by mimicking Claude Code’s OAuth + request headers.

- Token endpoint: `https://console.anthropic.com/v1/oauth/token`
- Requires beta/header matching (e.g. `anthropic-beta: claude-code-20250219,oauth-2025-04-20`, `x-app: cli`, `user-agent: claude-cli/...`)

---

## Roadmap

- Richer memory UX (memory tagging, confidence, and user controls in the UI).
- More provider backends and better UX around provider auth flows.
- Addon web UI only if panel limits are reached (for example: p95 panel load > 2s, heavy import/export workflows, or long-running analytics/background jobs).

## Contributing

Contributions welcome. PRs and issues are appreciated, especially around:
- new subscription provider backends
- improving safety/tooling defaults
- tests and local development workflows
- agent memory design and implementation

If you’re adding new tools, please keep them:
- opt-in per agent (tool allowlist)
- safe by default
- returning structured JSON objects (not pre-serialized JSON strings)

### Local dev harness

Run:

```bash
./scripts/dev_harness.sh
```

This creates/uses `.venv`, runs compile checks, Ruff, and unit tests.

### Local Home Assistant (Docker)

This repo includes a minimal Home Assistant instance for integration development.

Run:

```bash
./scripts/ha_dev.sh bootstrap
./scripts/ha_dev.sh logs
```

Then open `http://localhost:8123`.

Notes:
- The HA config lives in `dev/ha_config/` (state files are gitignored).
- The integration is bind-mounted into `/config/custom_components/ai_subscription_assist`.
- After Python code changes, restart HA with `./scripts/ha_dev.sh restart`.

---

## License

MIT — see [LICENSE](LICENSE).

---

*ai-subscription-assist is not affiliated with or endorsed by Anthropic, OpenAI, or Google.*
