"""Where the schema converter lives, which moved in Home Assistant 2026.9.

Core swapped `voluptuous-openapi` for `probatio` and renamed `convert` to
`to_openapi`. The call is the same either way -- a schema, an optional
`custom_serializer`, an OpenAPI schema back -- so only the import changes.

Importing it in one place is not tidiness. The two libraries each carry their
own `UNSUPPORTED` sentinel, and a `custom_serializer` returns one to say "I do
not handle this node". Core's serializer now returns *probatio's*; the old
converter compares against *its own* with `is`, does not recognise it, and
returns the sentinel as though it were a schema. The caller then reads it as a
mapping and every request dies on

    AttributeError: '_Unsupported' object has no attribute 'items'

with no tool ever reaching the model. Mixing the two halves is what breaks, so
the converter and the serializer have to come from the same library -- and the
way to guarantee that is to have exactly one line that decides which.

The fallback is not dead code: this integration supports Home Assistant back to
2026.2.2, where `probatio` does not exist.
"""

from __future__ import annotations

try:  # Home Assistant 2026.9 and later
    from probatio import to_openapi
except ImportError:  # Home Assistant 2026.8 and earlier
    from voluptuous_openapi import convert as to_openapi

__all__ = ["to_openapi"]
