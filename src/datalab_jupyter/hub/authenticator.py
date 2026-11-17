"""Authenticate JupyterHub users with single-use datalab tool launch grants."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from jupyterhub.auth import Authenticator
from jupyterhub.handlers.login import LoginHandler
from jupyterhub.utils import url_path_join
from tornado import web
from traitlets import Unicode

from datalab_jupyter.fragment_post import render_fragment_post

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


@dataclass(frozen=True)
class _ExchangeResponse:
    user_id: str
    display_name: str | None
    role: str
    group_ids: tuple[str, ...]
    tool_access_token: str
    expires_at: str
    notebook_launch_code: str | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> "_ExchangeResponse":
        if not isinstance(payload, dict):
            raise ValueError("response must be an object")
        required = {
            "user_id",
            "display_name",
            "role",
            "group_ids",
            "tool_access_token",
            "expires_at",
        }
        if not required.issubset(payload):
            raise ValueError("response omitted required fields")

        user_id = payload["user_id"]
        display_name = payload["display_name"]
        role = payload["role"]
        group_ids = payload["group_ids"]
        tool_access_token = payload["tool_access_token"]
        expires_at = payload["expires_at"]
        notebook_launch_code = payload.get("notebook_launch_code")
        if (
            not isinstance(user_id, str)
            or not user_id
            or display_name is not None
            and not isinstance(display_name, str)
            or not isinstance(role, str)
            or not role
            or not isinstance(group_ids, list)
            or not all(isinstance(group_id, str) for group_id in group_ids)
            or not isinstance(tool_access_token, str)
            or not tool_access_token
            or not isinstance(expires_at, str)
            or notebook_launch_code is not None
            and (not isinstance(notebook_launch_code, str) or not notebook_launch_code)
        ):
            raise ValueError("response contained invalid fields")
        _expiration(expires_at)
        return cls(
            user_id=user_id,
            display_name=display_name,
            role=role,
            group_ids=tuple(group_ids),
            tool_access_token=tool_access_token,
            expires_at=expires_at,
            notebook_launch_code=notebook_launch_code,
        )


class DatalabLoginHandler(LoginHandler):
    """Always exchange a fresh launch code, even when a Hub cookie exists."""

    async def get(self) -> None:
        """Render the credential-free fragment-to-POST bootstrap."""

        self.finish(render_fragment_post(self, "datalab_launch_code"))

    async def post(self) -> None:
        """Authenticate from a launch code submitted in the XSRF-protected body."""

        self.statsd.incr("login.request")
        code = (self.get_body_argument("datalab_launch_code", None) or "").strip()
        if not code:
            raise web.HTTPError(403, "Invalid or expired datalab launch code")

        await self._authenticate_and_redirect(code)

    async def _authenticate_and_redirect(self, code: str) -> None:
        """Exchange one code, refresh the user server, and complete the handoff."""

        self.set_header("Cache-Control", "no-store")
        self.set_header("Referrer-Policy", "no-referrer")
        self._datalab_launch_code = code
        try:
            user = await self.login_user()
        finally:
            del self._datalab_launch_code
        if user is None:
            raise web.HTTPError(403, "Invalid or expired datalab launch code")

        auth_state = await user.get_auth_state()
        notebook_launch_code = auth_state.get("notebook_launch_code") if auth_state else None
        needs_selected_items_server = bool(
            isinstance(notebook_launch_code, str) and notebook_launch_code
        )
        await self._ensure_fresh_server(user, required=needs_selected_items_server)
        next_url = self.get_next_url(user)
        if isinstance(notebook_launch_code, str) and notebook_launch_code:
            next_url = (
                f"{url_path_join(user.url, 'datalab/open-selected')}#"
                f"{urlencode({'notebook_launch_code': notebook_launch_code})}"
            )
        self.redirect(next_url, permanent=False)

    async def _ensure_fresh_server(self, user: Any, *, required: bool) -> None:
        """Replace an existing server so it receives the newly saved auth state."""

        spawner = user.spawners[""]
        replace_server = bool(spawner.active)
        pending = spawner.pending

        if pending == "spawn":
            await self._await_server_future(
                spawner._spawn_future,
                "The existing Jupyter server did not finish starting",
            )
            if not spawner.ready:
                raise web.HTTPError(503, "The existing Jupyter server did not become ready")
        elif pending == "stop":
            await self._await_server_future(
                spawner._stop_future,
                "The existing Jupyter server did not finish stopping",
            )
            await self._verify_server_stopped(spawner)
        elif pending:
            raise web.HTTPError(503, "The existing Jupyter server is busy")

        if replace_server and spawner.ready:
            try:
                stop_future = await self.stop_single_user(user)
            except Exception as exc:
                raise web.HTTPError(503, "Unable to stop the existing Jupyter server") from exc
            await self._await_server_future(
                stop_future,
                "The existing Jupyter server did not finish stopping",
            )
            await self._verify_server_stopped(spawner)

        if replace_server or required:
            try:
                await self.spawn_single_user(user)
                replacement_spawner = user.spawners[""]
                if replacement_spawner._spawn_future is not None:
                    await replacement_spawner._spawn_future
            except Exception as exc:
                raise web.HTTPError(503, "Unable to start the fresh Jupyter server") from exc
            if not replacement_spawner.ready:
                raise web.HTTPError(503, "The fresh Jupyter server did not become ready")

    @staticmethod
    async def _await_server_future(future: Any, failure_message: str) -> None:
        """Await a Hub lifecycle future and normalize failures for the launch flow."""

        if future is None:
            raise web.HTTPError(503, failure_message)
        try:
            await future
        except Exception as exc:
            raise web.HTTPError(503, failure_message) from exc

    @staticmethod
    async def _verify_server_stopped(spawner: Any) -> None:
        """Fail closed unless the previous server process has actually stopped."""

        if spawner.active:
            raise web.HTTPError(503, "The existing Jupyter server did not stop")
        try:
            status = await spawner.poll()
        except Exception as exc:
            raise web.HTTPError(503, "Unable to verify that the Jupyter server stopped") from exc
        if status is None:
            raise web.HTTPError(503, "The existing Jupyter server is still running")


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

        try:
            exchange = _ExchangeResponse.from_payload(payload)
        except ValueError as exc:
            self.log.error("Invalid datalab launch response: %s", exc)
            raise web.HTTPError(502, "datalab returned invalid authentication data") from exc
        if _is_expired(exchange.expires_at):
            return None

        api_url = self.api_url.rstrip("/")
        username = f"datalab-{exchange.user_id}"
        current_user = {
            "id": exchange.user_id,
            "username": username,
            "display_name": exchange.display_name,
            "role": exchange.role,
            "group_ids": list(exchange.group_ids),
        }
        auth_state = {
            "tool_access_token": exchange.tool_access_token,
            "api_url": api_url,
            "current_user": current_user,
            "expires_at": exchange.expires_at,
        }
        if exchange.notebook_launch_code is not None:
            auth_state["notebook_launch_code"] = exchange.notebook_launch_code

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
