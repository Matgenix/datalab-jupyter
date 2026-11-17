"""Render a same-origin bootstrap that moves one URL fragment value into a POST."""

import html
import json
import re
import secrets
from typing import Any

_FIELD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def render_fragment_post(handler: Any, fragment_field: str) -> str:
    """Return a no-store page that submits one fragment field with XSRF protection."""

    if not _FIELD_NAME.fullmatch(fragment_field):
        raise ValueError("fragment_field must be a valid form field name")

    nonce = secrets.token_urlsafe(18)
    xsrf_token = handler.xsrf_token
    if isinstance(xsrf_token, bytes):
        xsrf_token = xsrf_token.decode("utf-8")

    handler.set_header("Cache-Control", "no-store")
    handler.set_header("Referrer-Policy", "no-referrer")
    handler.set_header(
        "Content-Security-Policy",
        "; ".join(
            (
                "default-src 'none'",
                f"script-src 'nonce-{nonce}'",
                "form-action 'self'",
                "base-uri 'none'",
                "frame-ancestors 'none'",
            )
        ),
    )

    action = html.escape(handler.request.path, quote=True)
    field = html.escape(fragment_field, quote=True)
    token = html.escape(str(xsrf_token), quote=True)
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
