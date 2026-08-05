"""Datalab integration for JupyterHub and Jupyter user servers."""


def _jupyter_server_extension_points() -> list[dict[str, str]]:
    """Expose the user-server integration without importing optional dependencies."""

    return [{"module": "datalab_jupyter.server.extension"}]
