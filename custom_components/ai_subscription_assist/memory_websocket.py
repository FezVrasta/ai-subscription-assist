"""WebSocket API for memory/session management panel."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.websocket_api import ActiveConnection
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    DATA_MEMORY_SERVICES,
    DATA_MEMORY_WS_REGISTERED,
    DOMAIN,
)
from .memory_contract import (
    WS_TYPE_ENTRY_LIST,
    WS_TYPE_MEMORY_CLEAR,
    WS_TYPE_MEMORY_DELETE,
    WS_TYPE_MEMORY_LIST,
    WS_TYPE_MEMORY_STATUS,
    WS_TYPE_SESSION_CLEAR,
    WS_TYPE_SESSION_GET,
    WS_TYPE_SESSION_LIST,
)
from .memory_service import AiSubscriptionAssistMemoryService

WS_ERR_DOMAIN = f"{DOMAIN}_error"
WS_ERR_NOT_FOUND = "not_found"
WS_ERR_INVALID = "invalid_format"


def _domain_data(hass: HomeAssistant) -> dict[str, Any]:
    return hass.data.setdefault(DOMAIN, {})


def _memory_services(hass: HomeAssistant) -> dict[str, AiSubscriptionAssistMemoryService]:
    services = _domain_data(hass).get(DATA_MEMORY_SERVICES)
    if isinstance(services, dict):
        return services
    return {}


def _resolve_service(
    hass: HomeAssistant, config_entry_id: str | None
) -> tuple[str, AiSubscriptionAssistMemoryService]:
    services = _memory_services(hass)
    if config_entry_id:
        selected = services.get(config_entry_id)
        if selected is None:
            raise HomeAssistantError(
                f"Config entry '{config_entry_id}' is not loaded for {DOMAIN}."
            )
        return config_entry_id, selected

    if not services:
        raise HomeAssistantError("No loaded AI Subscription Assist entries found.")

    if len(services) == 1:
        entry_id, selected = next(iter(services.items()))
        return entry_id, selected

    raise HomeAssistantError(
        "Multiple AI Subscription Assist entries are loaded. Provide config_entry_id."
    )


def _user_id(connection: ActiveConnection) -> str | None:
    user = connection.user
    if user is None:
        return None
    return user.id


def _send_error(connection: ActiveConnection, msg: dict[str, Any], error: Exception) -> None:
    connection.send_error(msg["id"], WS_ERR_DOMAIN, str(error))


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_TYPE_ENTRY_LIST,
    }
)
@websocket_api.async_response
async def ws_entry_list(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return loaded ai_subscription_assist config entries and conversation subentries."""
    entries_payload: list[dict[str, Any]] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        subentries_payload: list[dict[str, Any]] = []
        for subentry in getattr(entry, "subentries", {}).values():
            if subentry.subentry_type != "conversation":
                continue
            subentries_payload.append(
                {
                    "subentry_id": subentry.subentry_id,
                    "title": subentry.title,
                    # ConfigSubentry has no disabled_by on HA 2026.8; reading it
                    # directly raises AttributeError and breaks the whole panel.
                    "disabled": getattr(subentry, "disabled_by", None) is not None,
                }
            )

        entries_payload.append(
            {
                "entry_id": entry.entry_id,
                "title": entry.title,
                "state": str(entry.state),
                "subentries": subentries_payload,
            }
        )

    entries_payload.sort(key=lambda item: str(item.get("title", "")).lower())
    connection.send_result(msg["id"], {"entries": entries_payload, "count": len(entries_payload)})


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_TYPE_MEMORY_STATUS,
        vol.Optional("config_entry_id"): cv.string,
    }
)
@websocket_api.async_response
async def ws_memory_status(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return memory/session settings and counters."""
    try:
        _, memory_service = _resolve_service(hass, msg.get("config_entry_id"))
        requester_user_id = _user_id(connection)
        is_admin = await memory_service.async_user_is_admin(requester_user_id)
        snapshot = await memory_service.async_status_snapshot(
            requester_user_id, is_admin=is_admin
        )
        connection.send_result(msg["id"], snapshot)
    except HomeAssistantError as err:
        _send_error(connection, msg, err)


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_TYPE_MEMORY_LIST,
        vol.Optional("config_entry_id"): cv.string,
        vol.Optional("scope", default="mine"): vol.In({"mine", "shared", "all"}),
        vol.Optional("limit", default=50): vol.All(vol.Coerce(int), vol.Range(min=1, max=500)),
        vol.Optional("target_user_id"): cv.string,
    }
)
@websocket_api.async_response
async def ws_memory_list(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """List memory items for panel use."""
    try:
        entry_id, memory_service = _resolve_service(hass, msg.get("config_entry_id"))
        requester_user_id = _user_id(connection)
        is_admin = await memory_service.async_user_is_admin(requester_user_id)
        items = await memory_service.async_list_memory_items(
            scope=str(msg.get("scope", "mine")),
            limit=int(msg.get("limit", 50)),
            requester_user_id=requester_user_id,
            is_admin=is_admin,
            target_user_id=msg.get("target_user_id"),
        )
        connection.send_result(
            msg["id"],
            {
                "entry_id": entry_id,
                "scope": str(msg.get("scope", "mine")),
                "target_user_id": msg.get("target_user_id"),
                "count": len(items),
                "items": items,
            },
        )
    except HomeAssistantError as err:
        _send_error(connection, msg, err)


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_TYPE_MEMORY_DELETE,
        vol.Optional("config_entry_id"): cv.string,
        vol.Required("memory_id"): cv.string,
    }
)
@websocket_api.async_response
async def ws_memory_delete(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Delete one memory item by id."""
    try:
        entry_id, memory_service = _resolve_service(hass, msg.get("config_entry_id"))
        requester_user_id = _user_id(connection)
        is_admin = await memory_service.async_user_is_admin(requester_user_id)
        memory_id = str(msg["memory_id"])
        deleted, scope = await memory_service.async_delete_memory_item(
            memory_id,
            requester_user_id,
            is_admin=is_admin,
        )
        connection.send_result(
            msg["id"],
            {
                "entry_id": entry_id,
                "memory_id": memory_id,
                "deleted": bool(deleted),
                "scope": scope,
            },
        )
    except HomeAssistantError as err:
        _send_error(connection, msg, err)


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_TYPE_MEMORY_CLEAR,
        vol.Optional("config_entry_id"): cv.string,
        vol.Optional("scope", default="mine"): vol.In({"mine", "shared", "all"}),
        vol.Optional("target_user_id"): cv.string,
        vol.Required("confirm"): bool,
    }
)
@websocket_api.async_response
async def ws_memory_clear(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Clear memories in the requested scope."""
    if not bool(msg.get("confirm", False)):
        connection.send_error(msg["id"], WS_ERR_INVALID, "Set confirm=true to clear memories.")
        return

    try:
        entry_id, memory_service = _resolve_service(hass, msg.get("config_entry_id"))
        requester_user_id = _user_id(connection)
        is_admin = await memory_service.async_user_is_admin(requester_user_id)
        scope = str(msg.get("scope", "mine"))
        target_user_id = msg.get("target_user_id")
        removed = await memory_service.async_clear_memory_items(
            scope=scope,
            requester_user_id=requester_user_id,
            is_admin=is_admin,
            target_user_id=target_user_id,
        )
        connection.send_result(
            msg["id"],
            {
                "entry_id": entry_id,
                "scope": scope,
                "target_user_id": target_user_id,
                "removed": removed,
            },
        )
    except HomeAssistantError as err:
        _send_error(connection, msg, err)


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_TYPE_SESSION_LIST,
        vol.Optional("config_entry_id"): cv.string,
        vol.Optional("scope", default="mine"): vol.In({"mine", "all"}),
        vol.Optional("subentry_id"): cv.string,
        vol.Optional("target_user_id"): cv.string,
        vol.Optional("limit", default=50): vol.All(vol.Coerce(int), vol.Range(min=1, max=500)),
    }
)
@websocket_api.async_response
async def ws_session_list(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """List resumable sessions."""
    try:
        entry_id, memory_service = _resolve_service(hass, msg.get("config_entry_id"))
        requester_user_id = _user_id(connection)
        is_admin = await memory_service.async_user_is_admin(requester_user_id)
        sessions = await memory_service.async_list_sessions(
            scope=str(msg.get("scope", "mine")),
            limit=int(msg.get("limit", 50)),
            requester_user_id=requester_user_id,
            is_admin=is_admin,
            subentry_id=msg.get("subentry_id"),
            target_user_id=msg.get("target_user_id"),
        )
        connection.send_result(
            msg["id"],
            {
                "entry_id": entry_id,
                "scope": str(msg.get("scope", "mine")),
                "subentry_id": msg.get("subentry_id"),
                "target_user_id": msg.get("target_user_id"),
                "count": len(sessions),
                "sessions": sessions,
            },
        )
    except HomeAssistantError as err:
        _send_error(connection, msg, err)


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_TYPE_SESSION_GET,
        vol.Optional("config_entry_id"): cv.string,
        vol.Required("session_id"): cv.string,
        vol.Optional("limit", default=100): vol.All(vol.Coerce(int), vol.Range(min=1, max=500)),
    }
)
@websocket_api.async_response
async def ws_session_get(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Fetch one resumable session detail."""
    try:
        entry_id, memory_service = _resolve_service(hass, msg.get("config_entry_id"))
        requester_user_id = _user_id(connection)
        is_admin = await memory_service.async_user_is_admin(requester_user_id)
        session_id = str(msg["session_id"])
        session = await memory_service.async_get_session(
            session_id=session_id,
            requester_user_id=requester_user_id,
            is_admin=is_admin,
            limit=int(msg.get("limit", 100)),
        )
        if session is None:
            connection.send_error(
                msg["id"],
                WS_ERR_NOT_FOUND,
                "Session not found or not permitted.",
            )
            return
        connection.send_result(msg["id"], {"entry_id": entry_id, "session": session})
    except HomeAssistantError as err:
        _send_error(connection, msg, err)


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_TYPE_SESSION_CLEAR,
        vol.Optional("config_entry_id"): cv.string,
        vol.Optional("scope", default="mine"): vol.In({"mine", "all"}),
        vol.Optional("subentry_id"): cv.string,
        vol.Optional("target_user_id"): cv.string,
        vol.Optional("session_id"): cv.string,
        vol.Required("confirm"): bool,
    }
)
@websocket_api.async_response
async def ws_session_clear(
    hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]
) -> None:
    """Clear sessions by scope, user, subentry, or session id."""
    if not bool(msg.get("confirm", False)):
        connection.send_error(msg["id"], WS_ERR_INVALID, "Set confirm=true to clear sessions.")
        return

    try:
        entry_id, memory_service = _resolve_service(hass, msg.get("config_entry_id"))
        requester_user_id = _user_id(connection)
        is_admin = await memory_service.async_user_is_admin(requester_user_id)
        scope = str(msg.get("scope", "mine"))
        subentry_id = msg.get("subentry_id")
        target_user_id = msg.get("target_user_id")
        session_id = msg.get("session_id")
        removed_sessions, removed_messages = await memory_service.async_clear_sessions(
            requester_user_id=requester_user_id,
            is_admin=is_admin,
            scope=scope,
            subentry_id=subentry_id,
            target_user_id=target_user_id,
            session_id=session_id,
        )
        connection.send_result(
            msg["id"],
            {
                "entry_id": entry_id,
                "scope": scope,
                "subentry_id": subentry_id,
                "target_user_id": target_user_id,
                "session_id": session_id,
                "removed_sessions": removed_sessions,
                "removed_messages": removed_messages,
            },
        )
    except HomeAssistantError as err:
        _send_error(connection, msg, err)


def async_setup_memory_websocket_api(hass: HomeAssistant) -> None:
    """Register memory/session websocket commands once."""
    domain_data = _domain_data(hass)
    if domain_data.get(DATA_MEMORY_WS_REGISTERED):
        return

    websocket_api.async_register_command(hass, ws_entry_list)
    websocket_api.async_register_command(hass, ws_memory_status)
    websocket_api.async_register_command(hass, ws_memory_list)
    websocket_api.async_register_command(hass, ws_memory_delete)
    websocket_api.async_register_command(hass, ws_memory_clear)
    websocket_api.async_register_command(hass, ws_session_list)
    websocket_api.async_register_command(hass, ws_session_get)
    websocket_api.async_register_command(hass, ws_session_clear)

    domain_data[DATA_MEMORY_WS_REGISTERED] = True
