"""Shared browser URL handling for the managed JupyterHub."""

import os
from urllib.parse import urlsplit


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
