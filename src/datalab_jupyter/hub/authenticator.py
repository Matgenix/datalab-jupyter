"""Authenticate JupyterHub users with single-use datalab tool launch grants."""

import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from jupyterhub.auth import Authenticator
from jupyterhub.handlers.login import LoginHandler
from jupyterhub.utils import url_path_join
from tornado import web
from traitlets import Unicode

EXCHANGE_PATH = "/v0.1/tools/plugins/jupyter/exchange"


def _expiration(value: str) -> datetime:
    """Parse a timezone-aware delegated tool session expiration timestamp."""

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("expires_at must include a timezone")
    return parsed


def _is_expired(value: str) -> bool:
    """Return whether a delegated tool session has reached its expiration."""

    return _expiration(value) <= datetime.now(timezone.utc)


class DatalabLoginHandler(LoginHandler):
    """Always exchange a fresh launch code, even when a Hub cookie exists."""

    async def get(self) -> None:
        """Authenticate from the launch URL and replace any previous Hub identity."""

        self.statsd.incr("login.request")
        code = (self.get_argument("datalab_launch_code", None) or "").strip()
        if not code:
            raise web.HTTPError(403, "Invalid or expired datalab launch code")

        # Tornado's access log uses request.uri after the handler completes. Keep
        # the single-use launch code available only in memory while redacting it
        # from that log representation.
        self._datalab_launch_code = code
        parts = urlsplit(self.request.uri)
        safe_query = urlencode(
            [
                (key, value)
                for key, value in parse_qsl(parts.query, keep_blank_values=True)
                if key != "datalab_launch_code"
            ]
        )
        self.request.uri = urlunsplit(("", "", parts.path, safe_query, ""))
        self.set_header("Cache-Control", "no-store")
        self.set_header("Referrer-Policy", "no-referrer")

        user = await self.login_user()
        if user is None:
            raise web.HTTPError(403, "Invalid or expired datalab launch code")

        next_url = self.get_next_url(user)
        auth_state = await user.get_auth_state()
        notebook_launch_code = auth_state.get("notebook_launch_code") if auth_state else None
        if isinstance(notebook_launch_code, str) and notebook_launch_code:
            spawner = user.spawners[""]
            if not spawner.ready:
                if not spawner.pending:
                    await self.spawn_single_user(user)
                if spawner._spawn_future is not None:
                    await spawner._spawn_future
                if not spawner.ready:
                    raise web.HTTPError(503, "The Jupyter server did not become ready")
            next_url = (
                f"{url_path_join(user.url, 'datalab/open-selected')}?"
                f"{urlencode({'notebook_launch_code': notebook_launch_code})}"
            )
        self.redirect(next_url, permanent=False)


class DatalabAuthenticator(Authenticator):
    """Exchange a datalab launch code for a tool access token."""

    api_url = Unicode(config=True, help="Internal datalab API URL.")
    client_id = Unicode(config=True, help="JupyterHub integration client identifier.")
    client_secret = Unicode(config=True, help="JupyterHub integration client secret.")

    def login_url(self, base_url: str) -> str:
        """Return the handler that always performs launch-code authentication."""

        return url_path_join(base_url, "datalab-login")

    def get_handlers(self, app: Any) -> list[tuple[str, type[DatalabLoginHandler]]]:
        """Register the forced-login handler below the Hub base URL."""

        return [("/datalab-login", DatalabLoginHandler)]

    async def authenticate(
        self, handler: Any, data: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        """Exchange a launch code for datalab identity and a delegated tool session."""

        code = (getattr(handler, "_datalab_launch_code", None) or "").strip()
        if not code:
            return None

        exchange_url = f"{self.api_url.rstrip('/')}/{EXCHANGE_PATH.lstrip('/')}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    exchange_url,
                    json={"code": code},
                    auth=httpx.BasicAuth(self.client_id, self.client_secret),
                )
        except httpx.RequestError as exc:
            self.log.error("Unable to reach the datalab launch code exchange endpoint: %s", exc)
            raise web.HTTPError(503, "datalab authentication is temporarily unavailable") from exc

        if response.status_code in {400, 401, 403, 404, 409, 410}:
            self.log.warning("datalab rejected a Jupyter launch code (%s)", response.status_code)
            return None

        try:
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPStatusError, ValueError) as exc:
            self.log.error("Invalid datalab launch code exchange response: %s", exc)
            raise web.HTTPError(502, "datalab returned an invalid authentication response") from exc

        required_fields = {
            "user_id",
            "display_name",
            "role",
            "group_ids",
            "tool_access_token",
            "expires_at",
        }
        if not isinstance(payload, dict) or not required_fields.issubset(payload):
            self.log.error("datalab launch response omitted required fields")
            raise web.HTTPError(502, "datalab returned an incomplete authentication response")

        user_id = payload["user_id"]
        username = f"datalab-{user_id}"
        tool_access_token = payload["tool_access_token"]
        role = payload["role"]
        display_name = payload["display_name"]
        group_ids = payload["group_ids"]
        expires_at = payload["expires_at"]
        notebook_launch_code = payload.get("notebook_launch_code")
        if (
            not isinstance(user_id, str)
            or not user_id
            or not isinstance(tool_access_token, str)
            or not tool_access_token
            or not isinstance(role, str)
            or not role
            or display_name is not None
            and not isinstance(display_name, str)
            or not isinstance(group_ids, list)
            or not all(isinstance(group_id, str) for group_id in group_ids)
            or not isinstance(expires_at, str)
            or notebook_launch_code is not None
            and (not isinstance(notebook_launch_code, str) or not notebook_launch_code)
        ):
            raise web.HTTPError(502, "datalab returned invalid authentication data")
        try:
            if _is_expired(expires_at):
                return None
        except ValueError as exc:
            raise web.HTTPError(502, "datalab returned an invalid expiration") from exc

        api_url = self.api_url.rstrip("/")
        current_user = {
            "id": user_id,
            "username": username,
            "display_name": display_name,
            "role": role,
            "group_ids": group_ids,
        }
        auth_state = {
            "tool_access_token": tool_access_token,
            "api_url": api_url,
            "current_user": current_user,
            "expires_at": expires_at,
        }
        if notebook_launch_code is not None:
            auth_state["notebook_launch_code"] = notebook_launch_code

        return {
            "name": username,
            "admin": False,
            "auth_state": auth_state,
        }

    async def pre_spawn_start(self, user: Any, spawner: Any) -> None:
        """Inject only delegated datalab state into this single-user server."""

        auth_state = await user.get_auth_state()
        if not auth_state or not auth_state.get("tool_access_token"):
            raise web.HTTPError(
                403, "The delegated tool session has expired; launch JupyterLab again"
            )
        try:
            expired = _is_expired(str(auth_state["expires_at"]))
        except (KeyError, ValueError) as exc:
            raise web.HTTPError(403, "The delegated tool session is invalid; launch again") from exc
        if expired:
            raise web.HTTPError(
                403, "The delegated tool session has expired; launch JupyterLab again"
            )

        spawner.environment.update(
            {
                "DATALAB_API_KEY": str(auth_state["tool_access_token"]),
                "DATALAB_API_URL": str(auth_state["api_url"]),
                "DATALAB_CURRENT_USER_JSON": json.dumps(
                    auth_state["current_user"], separators=(",", ":")
                ),
            }
        )
