"""Datalab tool provider and JupyterHub launch-code exchange."""

import hmac
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import urlencode

from flask import Blueprint, jsonify, request
from pydatalab.config import CONFIG
from pydatalab.tools import (
    ItemTableSelectionAction,
    ToolContext,
    ToolLaunchGrantIssuer,
    ToolMetadata,
    ToolProvider,
    ToolRouteAuth,
    exchange_launch_code,
    issue_tool_selection_code,
)

from datalab_jupyter.config import PluginSettings, load_plugin_settings

JUPYTER_BLUEPRINT = Blueprint("jupyter-tool", __name__)


def _distribution_version() -> str:
    try:
        return version("datalab-jupyter")
    except PackageNotFoundError:
        return "0.1.0"


def _login_url(code: str, settings: PluginSettings) -> str:
    base_url = settings.browser_url(CONFIG.APP_URL)
    login_url = f"{base_url.rstrip('/')}/hub/datalab-login"
    return f"{login_url}?{urlencode({'datalab_launch_code': code})}"


class JupyterToolProvider(ToolProvider):
    """Standalone JupyterLab provider installed through the tool-plugin registry."""

    id = "jupyter"
    metadata = ToolMetadata(
        name="JupyterLab",
        description="Explore and analyse your datalab data programmatically in JupyterLab.",
        version=_distribution_version(),
        icon="book",
        launch_actions=(
            ItemTableSelectionAction(
                id="open-in-notebook",
                label="Open in notebook",
                tables=("samples", "inventory", "equipment", "collection-items"),
                min_items=1,
                max_items=20,
            ),
        ),
    )
    blueprint = JUPYTER_BLUEPRINT
    route_auth = ToolRouteAuth.SERVICE

    def __init__(self) -> None:
        self.settings = load_plugin_settings()

    def authenticate_service_request(self) -> bool:
        """Authenticate the configured JupyterHub client."""

        authorization = request.authorization
        if authorization is None or authorization.type.lower() != "basic":
            return False
        return hmac.compare_digest(
            (authorization.username or "").encode(), self.settings.client_id.encode()
        ) and hmac.compare_digest(
            (authorization.password or "").encode(), self.settings.client_secret.encode()
        )

    def launch(self, context: ToolContext, grants: ToolLaunchGrantIssuer) -> str:
        code = grants.issue(self.settings.client_id)
        return _login_url(code, self.settings)


@JUPYTER_BLUEPRINT.route("/exchange", methods=["POST"])
def exchange_jupyter_launch_code():
    """Exchange one launch code for a delegated Datalab tool session."""

    payload = request.get_json(silent=True) or {}
    code = payload.get("code")
    if not isinstance(code, str) or not code:
        return jsonify({"status": "error", "message": "A launch code is required"}), 400

    settings = load_plugin_settings()
    exchange = exchange_launch_code(code, JupyterToolProvider.id, settings.client_id)
    if exchange is None:
        return jsonify({"status": "error", "message": "Invalid or expired launch code"}), 400

    response_payload = {
        "user_id": exchange.context.user_id,
        "display_name": exchange.context.display_name,
        "role": exchange.context.role,
        "group_ids": list(exchange.context.group_ids),
        "tool_access_token": exchange.tool_session.tool_access_token,
        "expires_at": exchange.tool_session.expires_at.isoformat(),
    }
    if exchange.selection is not None:
        response_payload["notebook_launch_code"] = issue_tool_selection_code(
            user_id=exchange.context.user_id,
            tool_id=JupyterToolProvider.id,
            selection=exchange.selection,
        )

    response = jsonify(response_payload)
    response.headers["Cache-Control"] = "no-store"
    return response, 200
