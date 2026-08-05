"""Healthcheck for a JupyterHub mounted at a configurable public path."""

from urllib.request import urlopen

from .urls import configured_base_url


def main() -> None:
    """Fail unless the local Hub health endpoint responds successfully."""

    url = f"http://localhost:8000{configured_base_url()}hub/health"
    with urlopen(url, timeout=5):  # noqa: S310 - fixed localhost endpoint
        pass
