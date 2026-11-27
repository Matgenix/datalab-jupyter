"""Render protected same-origin pages for the JupyterLab launch handoff."""

import html
import json
import re
import secrets
from typing import Any
from urllib.parse import urlsplit

_FIELD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _xsrf_token(handler: Any) -> str:
    token = handler.xsrf_token
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return html.escape(str(token), quote=True)


def set_launch_page_headers(handler: Any, *, script_nonce: str | None = None) -> None:
    directives = ["default-src 'none'"]
    if script_nonce is not None:
        directives.append(f"script-src 'nonce-{script_nonce}'")
    directives.extend(("form-action 'self'", "base-uri 'none'", "frame-ancestors 'none'"))
    handler.set_header("Cache-Control", "no-store")
    handler.set_header("Referrer-Policy", "no-referrer")
    handler.set_header("X-Content-Type-Options", "nosniff")
    handler.set_header("Content-Security-Policy", "; ".join(directives))


def render_fragment_post(handler: Any, fragment_field: str) -> str:
    """Return a no-store page that submits one fragment field with XSRF protection."""

    if not _FIELD_NAME.fullmatch(fragment_field):
        raise ValueError("fragment_field must be a valid form field name")

    nonce = secrets.token_urlsafe(18)
    set_launch_page_headers(handler, script_nonce=nonce)

    action = html.escape(handler.request.path, quote=True)
    field = html.escape(fragment_field, quote=True)
    token = _xsrf_token(handler)
    script_field = json.dumps(fragment_field)
    return rf"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Opening JupyterLab</title></head>
<body>
<p id="fragment-error" hidden>Unable to continue this JupyterLab launch.</p>
<noscript>JavaScript is required to continue this JupyterLab launch.</noscript>
<form id="fragment-post" method="post" action="{action}">
<input type="hidden" name="_xsrf" value="{token}">
<input type="hidden" name="{field}" value="">
</form>
<script nonce="{nonce}">
"use strict";
const fragment = window.location.hash.slice(1);
history.replaceState(null, "", window.location.pathname + window.location.search);
const expectedField = {script_field};
let malformedEncoding = /%(?![0-9A-Fa-f]{{2}})/.test(fragment);
try {{
  decodeURIComponent(fragment.replace(/\+/g, " "));
}} catch (_error) {{
  malformedEncoding = true;
}}
const entries = Array.from(new URLSearchParams(fragment).entries());
if (
  malformedEncoding ||
  entries.length !== 1 ||
  entries[0][0] !== expectedField ||
  entries[0][1].length === 0
) {{
  document.getElementById("fragment-error").hidden = false;
}} else {{
  const form = document.getElementById("fragment-post");
  form.elements.namedItem(expectedField).value = entries[0][1];
  form.submit();
}}
</script>
</body>
</html>
"""


def render_navigation_page(handler: Any, target_url: str) -> str:
    """Return a protected page that navigates to one trusted HTTP(S) target."""

    if not isinstance(target_url, str) or not target_url or target_url != target_url.strip():
        raise ValueError("target_url must be an absolute HTTP(S) URL")
    parsed = urlsplit(target_url)
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("target_url must be an absolute HTTP(S) URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("target_url must be an absolute HTTP(S) URL")

    nonce = secrets.token_urlsafe(18)
    set_launch_page_headers(handler, script_nonce=nonce)
    target = html.escape(target_url, quote=True)
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Opening JupyterLab</title></head>
<body>
<p>Opening JupyterLab…</p>
<input id="navigation-target" type="hidden" value="{target}">
<script nonce="{nonce}">
"use strict";
window.location.replace(document.getElementById("navigation-target").value);
</script>
</body>
</html>
"""


def render_renewal_confirmation(handler: Any, authorization_code: str, binding: str) -> str:
    """Return the explicit near-expiry credential renewal choice."""

    set_launch_page_headers(handler)
    action = html.escape(handler.request.path, quote=True)
    token = _xsrf_token(handler)
    code = html.escape(authorization_code, quote=True)
    signed_binding = html.escape(binding, quote=True)
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Open JupyterLab</title></head>
<body>
<h1>Open JupyterLab</h1>
<p>The stored Datalab access credential associated with this JupyterHub login is nearing expiry.</p>
<p>Extending access preserves the same server and in-memory computation only when the credential
and running-server provenance match. Otherwise the server may restart. Continuing without
extension can also rotate the credential and restart the server when fewer than ten minutes
remain.</p>
<p>Extending access does not reset the server idle timeout or maximum server age. An invalid
credential or one at its hard age cap will be replaced and can restart the server.</p>
<form method="post" action="{action}">
<input type="hidden" name="_xsrf" value="{token}">
<input type="hidden" name="datalab_authorization_code" value="{code}">
<input type="hidden" name="renewal_binding" value="{signed_binding}">
<button type="submit" name="renew_existing_session" value="false">Continue without extending</button>
<button type="submit" name="renew_existing_session" value="true">Extend Datalab access and open JupyterLab</button>
</form>
</body>
</html>
"""
