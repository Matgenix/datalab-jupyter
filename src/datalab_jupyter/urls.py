"""Shared browser URL handling for the managed JupyterHub."""

import os
import secrets
from urllib.parse import quote, urlsplit


def configured_base_url() -> str:
    """Derive the Hub base path from the same public settings used by datalab."""

    public_url = os.environ.get("DATALAB_JUPYTER_PUBLIC_URL", "").strip()
    if public_url:
        path = urlsplit(public_url).path
    else:
        app_url = os.environ.get("PYDATALAB_APP_URL", "").strip()
        app_path = urlsplit(app_url).path.rstrip("/") if app_url else ""
        path = f"{app_path}/jupyter/"
    normalized_path = path.strip("/")
    return f"/{normalized_path}/" if normalized_path else "/"


def new_workspace_id() -> str:
    """Return a fresh URL-safe identifier for one JupyterLab workspace."""

    return secrets.token_urlsafe(16)


def workspace_url(
    server_base_url: str,
    *,
    workspace_id: str | None = None,
    notebook_path: str | None = None,
) -> str:
    """Build a native JupyterLab workspace URL below a user server base path."""

    workspace = quote(workspace_id or new_workspace_id(), safe="")
    destination = f"{server_base_url.rstrip('/')}/lab/workspaces/{workspace}"
    if notebook_path is not None:
        destination = f"{destination}/tree/{quote(notebook_path.lstrip('/'), safe='/')}"
    return destination
