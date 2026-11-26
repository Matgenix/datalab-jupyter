"""datalab tool provider and JupyterHub launch-code exchange."""

import hmac
import re
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
    ToolSessionPolicy,
    exchange_authorized_service_launch_code,
    issue_tool_selection_code,
)

from datalab_jupyter.config import (
    PluginSettings,
    load_plugin_settings,
    validate_http_base_url,
)

JUPYTER_BLUEPRINT = Blueprint("jupyter-tool", __name__)
_MAX_CANDIDATE_TOKEN_LENGTH = 4096
_HANDOFF_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$", re.ASCII)
_PKCE_VERIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{43,128}$", re.ASCII)
_USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$", re.ASCII)


def _distribution_version() -> str:
    try:
        return version("datalab-jupyter")
    except PackageNotFoundError:
        return "0.1.0"


def _login_url(code: str, settings: PluginSettings) -> str:
    base_url = settings.browser_url(CONFIG.APP_URL)
    login_url = f"{base_url.rstrip('/')}/hub/datalab-login"
    return f"{login_url}#{urlencode({'datalab_launch_code': code})}"


def _callback_url(settings: PluginSettings) -> str:
    base_url = validate_http_base_url(
        settings.browser_url(CONFIG.APP_URL),
        name="Datalab Jupyter public URL",
    )
    return f"{base_url}/hub/datalab-callback"


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
        self.settings = load_plugin_settings(allow_insecure_http=CONFIG.TOOLS.ALLOW_INSECURE_HTTP)

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
        code = grants.issue_service_launch(
            self.settings.client_id,
            _callback_url(self.settings),
            lifetime_seconds=5 * 60,
            session_policy=ToolSessionPolicy.REUSE_OR_REPLACE,
            allow_session_renewal=True,
        )
        return _login_url(code, self.settings)


@JUPYTER_BLUEPRINT.after_request
def disable_exchange_caching(response):
    """Keep exchange credentials and results out of intermediary caches."""

    response.headers["Cache-Control"] = "no-store"
    return response


@JUPYTER_BLUEPRINT.route("/exchange", methods=["POST"])
def exchange_jupyter_launch_code():
    """Exchange one browser-authorized code for a delegated datalab tool session."""

    payload = request.get_json(silent=True)
    allowed_fields = {
        "authorization_code",
        "code_verifier",
        "expected_user_id",
        "renew_existing_session",
    }
    if (
        not isinstance(payload, dict)
        or not {"authorization_code", "code_verifier"}.issubset(payload)
        or not set(payload).issubset(allowed_fields)
    ):
        return jsonify({"status": "error", "message": "Invalid exchange request"}), 400
    authorization_code = payload.get("authorization_code")
    code_verifier = payload.get("code_verifier")
    expected_user_id = payload.get("expected_user_id")
    renew_existing_session = payload.get("renew_existing_session", False)
    if (
        not isinstance(authorization_code, str)
        or not _HANDOFF_CODE_PATTERN.fullmatch(authorization_code)
        or not isinstance(code_verifier, str)
        or not _PKCE_VERIFIER_PATTERN.fullmatch(code_verifier)
        or "expected_user_id" in payload
        and (
            not isinstance(expected_user_id, str)
            or not _USER_ID_PATTERN.fullmatch(expected_user_id)
        )
        or type(renew_existing_session) is not bool
    ):
        return jsonify({"status": "error", "message": "Invalid exchange request"}), 400

    settings = load_plugin_settings(allow_insecure_http=CONFIG.TOOLS.ALLOW_INSECURE_HTTP)
    candidate = request.headers.get("DATALAB-EXISTING-TOOL-TOKEN")
    if candidate is not None and len(candidate) > _MAX_CANDIDATE_TOKEN_LENGTH:
        candidate = None
    exchange = exchange_authorized_service_launch_code(
        authorization_code,
        code_verifier,
        JupyterToolProvider.id,
        settings.client_id,
        tool_session_lifetime_seconds=24 * 60 * 60,
        expected_user_id=expected_user_id,
        candidate_tool_access_token=candidate,
        minimum_reuse_lifetime_seconds=10 * 60,
        renew_existing_session=renew_existing_session,
    )
    if exchange is None:
        return jsonify({"status": "error", "message": "Invalid or expired authorization"}), 400

    response_payload = {
        "user_id": exchange.context.user_id,
        "display_name": exchange.context.display_name,
        "role": exchange.context.role,
        "group_ids": list(exchange.context.group_ids),
        "tool_access_token": exchange.tool_session.tool_access_token,
        "expires_at": exchange.tool_session.expires_at.isoformat(),
        "session_reused": exchange.session_reused,
        "session_renewed": exchange.session_renewed,
    }
    if exchange.selection is not None:
        response_payload["notebook_launch_code"] = issue_tool_selection_code(
            user_id=exchange.context.user_id,
            tool_id=JupyterToolProvider.id,
            selection=exchange.selection,
        )

    response = jsonify(response_payload)
    return response, 200
