"""Environment-owned configuration shared by the Datalab–Jupyter components."""

import os
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlsplit

ALLOW_INSECURE_HTTP_ENVIRONMENT = "PYDATALAB_TOOLS__ALLOW_INSECURE_HTTP"
_TRUE_ENVIRONMENT_VALUES = {"1", "true", "yes", "on"}
_FALSE_ENVIRONMENT_VALUES = {"", "0", "false", "no", "off"}


def _environment(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def allow_insecure_http_from_environment() -> bool:
    """Return the explicit deployment-wide insecure-HTTP testing exception."""
    value = os.environ.get(ALLOW_INSECURE_HTTP_ENVIRONMENT)
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized in _TRUE_ENVIRONMENT_VALUES:
        return True
    if normalized in _FALSE_ENVIRONMENT_VALUES:
        return False
    raise ValueError(f"{ALLOW_INSECURE_HTTP_ENVIRONMENT} must be a boolean")


def validate_http_base_url(
    value: str,
    *,
    name: str,
    allow_insecure: bool = False,
) -> str:
    """Validate and normalize one absolute HTTP(S) base URL."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be an absolute HTTP(S) base URL")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{name} must be an absolute HTTP(S) base URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port is not None
        and not 1 <= port <= 65535
    ):
        raise ValueError(f"{name} must be an absolute HTTP(S) base URL")
    hostname = parsed.hostname.rstrip(".").lower()
    try:
        is_loopback = ip_address(hostname).is_loopback
    except ValueError:
        is_loopback = hostname == "localhost" or hostname.endswith(".localhost")
    if parsed.scheme != "https" and not is_loopback and not allow_insecure:
        raise ValueError(f"{name} must use HTTPS unless it targets a loopback host")
    return value.rstrip("/")


def _http_base_url(name: str, *, allow_insecure: bool = False) -> str | None:
    value = _environment(name)
    if value is None:
        return None
    return validate_http_base_url(value, name=name, allow_insecure=allow_insecure)


@dataclass(frozen=True)
class PluginSettings:
    """Configuration used by the provider installed in the Datalab API."""

    client_id: str
    client_secret: str
    external_url: str | None
    public_url: str | None

    def browser_url(self, app_url: str | None) -> str:
        """Return the browser-facing JupyterHub base URL."""

        if self.external_url is not None:
            return self.external_url
        if self.public_url is not None:
            return self.public_url
        if app_url:
            return f"{app_url.rstrip('/')}/jupyter"
        return "http://localhost:8000/jupyter"


def load_plugin_settings(*, allow_insecure_http: bool = False) -> PluginSettings:
    """Load and validate the provider settings from the environment."""

    client_id = _environment("DATALAB_JUPYTER_CLIENT_ID") or "datalab-jupyter"
    raw_client_secret = os.environ.get("DATALAB_JUPYTER_CLIENT_SECRET", "")
    client_secret = raw_client_secret.strip()
    if not client_secret or client_secret != raw_client_secret or len(client_secret) < 32:
        raise ValueError("DATALAB_JUPYTER_CLIENT_SECRET must contain at least 32 characters")
    return PluginSettings(
        client_id=client_id,
        client_secret=client_secret,
        external_url=_http_base_url(
            "DATALAB_JUPYTER_EXTERNAL_URL", allow_insecure=allow_insecure_http
        ),
        public_url=_http_base_url("DATALAB_JUPYTER_PUBLIC_URL", allow_insecure=allow_insecure_http),
    )
