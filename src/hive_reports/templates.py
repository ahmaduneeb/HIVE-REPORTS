"""In-process template registry.

Other projects can register their own branded templates at import time:

    from hive_reports.templates import register_template

    @register_template("acme")
    def acme_template():
        return {
            "title": "INVOICE",
            "company": "ACME Corp",
            "address": "1 Looney Plaza",
            "footer": "Quack!",
            "show_qr": False,
            "page_size": "letter",
        }

Then anywhere in this package (CLI ``--template-name``, API
``template_name`` field, or programmatic ``render()``), ``"acme"`` resolves
to that template. DB-saved templates (via ``Store.upsert_template``) are
also resolvable, with the in-process registry taking precedence.
"""
from __future__ import annotations
from typing import Callable

from .pdf_render import DEFAULT_TEMPLATE

# Module-level registry. ``name -> template dict``.
_REGISTRY: dict[str, dict] = {}


def _coerce(body) -> dict:
    if callable(body) and not isinstance(body, dict):
        body = body()
    if not isinstance(body, dict):
        raise TypeError(
            f"template body must be a dict or a callable returning a dict; "
            f"got {type(body).__name__}"
        )
    return body


def register_template(name: str, body: dict | Callable[[], dict] | None = None):
    """Register a template under ``name``.

    Two forms are supported:

    1. Bare decorator on a zero-arg function returning a dict::

           @register_template("acme")
           def acme_template():
               return {"title": "INVOICE", ...}

    2. Direct call with a body (dict or factory)::

           register_template("acme", {"title": "INVOICE", ...})
           register_template("acme", lambda: {"title": "INVOICE", ...})

    When used as a decorator, the wrapped function is returned unchanged so
    callers can still call it directly if they want. Raises ``TypeError`` if
    ``body`` is neither a dict nor a callable returning a dict.
    """
    if not isinstance(name, str) or not name:
        raise TypeError("template name must be a non-empty string")

    # Decorator form: @register_template("acme") with a function below.
    if body is None:
        def decorator(fn: Callable[[], dict]) -> Callable[[], dict]:
            _REGISTRY[name] = _coerce(fn)
            return fn
        return decorator

    # Direct form: register_template("acme", body_or_factory).
    _REGISTRY[name] = _coerce(body)
    return _REGISTRY[name]


def get_registered_template(name: str) -> dict | None:
    """Return the in-process template registered under ``name`` (or None)."""
    return _REGISTRY.get(name)


def list_registered_templates() -> list[str]:
    """Return all in-process registered template names, sorted."""
    return sorted(_REGISTRY)


def resolve_template(template, store=None) -> dict | None:
    """Resolve ``template`` into a template dict.

    Accepted forms:

    * ``None`` → returns ``None`` (caller falls back to its own default).
    * ``dict`` → returned unchanged.
    * ``str`` (name) → looked up in the in-process registry first, then in
      the ``Store``'s saved templates (if a store was passed). Raises
      ``KeyError`` if not found.
    """
    if template is None:
        return None
    if isinstance(template, dict):
        return template
    if isinstance(template, str):
        registered = get_registered_template(template)
        if registered is not None:
            return registered
        if store is not None:
            saved = store.get_template(template)
            if saved is not None:
                return saved
        raise KeyError(f"template {template!r} not found (registered or in store)")
    raise TypeError(
        f"template must be a dict, str, or None; got {type(template).__name__}"
    )


# Make the existing default reachable by name everywhere.
register_template("default", DEFAULT_TEMPLATE)
