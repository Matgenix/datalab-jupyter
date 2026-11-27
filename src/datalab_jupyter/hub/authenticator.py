"""Authenticate JupyterHub users with single-use datalab tool launch grants."""

import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from jupyterhub.auth import Authenticator
from jupyterhub.handlers.login import LoginHandler
from jupyterhub.utils import url_path_join
from tornado import web
from traitlets import Unicode, validate

from datalab_jupyter.config import (
    allow_insecure_http_from_environment,
    validate_http_base_url,
)
from datalab_jupyter.fragment_post import (
    render_fragment_post,
    render_renewal_confirmation,
    set_launch_page_headers,
)
from datalab_jupyter.urls import workspace_url

EXCHANGE_PATH = "/v0.1/tools/plugins/jupyter/exchange"
BROWSER_AUTHORIZATION_PATH = "/v0.1/tools/jupyter/service-launch/authorize"
REUSE_CANDIDATE_HEADER = "DATALAB-EXISTING-TOOL-TOKEN"
_EXCHANGE_TIMEOUT_SECONDS = 10.0
_EXCHANGE_LOCK_WAIT_SECONDS = 2.0
_EXCHANGE_RETRY_AFTER_SECONDS = 2
_MAX_ADMITTED_EXCHANGES = 8
_CREDENTIAL_FINGERPRINT_ATTRIBUTE = "_datalab_credential_fingerprint"
_CREDENTIAL_FINGERPRINT_DOMAIN = b"datalab-jupyter:server-credential:v1\0"
_CONFIRMATION_DOMAIN = b"datalab-jupyter:renewal-confirmation:v1\0"
_CONFIRMATION_NAME = "datalab-jupyter-renewal"
_CONFIRMATION_MAX_AGE_DAYS = 5 * 60 / (24 * 60 * 60)
_RENEWAL_PROMPT_THRESHOLD = timedelta(hours=1)
_PKCE_COOKIE_NAME = "datalab-jupyter-pkce-verifier"
_PKCE_COOKIE_MAX_AGE_DAYS = 5 * 60 / (24 * 60 * 60)
_HANDOFF_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$", re.ASCII)
_PKCE_VERIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{43,128}$", re.ASCII)
_USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$", re.ASCII)
_INVALID_LAUNCH_MESSAGE = (
    "This launch is invalid or has expired. Return to Datalab and launch JupyterLab again."
)
_BUSY_LAUNCH_MESSAGE = (
    "Jupyter authentication is temporarily busy. Return to Datalab and launch JupyterLab again."
)


def _pkce_s256_challenge(code_verifier: str) -> str:
    """Return the unpadded RFC 7636 S256 challenge for a canonical verifier."""
    if not _PKCE_VERIFIER_PATTERN.fullmatch(code_verifier):
        raise ValueError("invalid PKCE code verifier")
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _expiration(value: str) -> datetime:
    """Parse a timezone-aware delegated tool session expiration timestamp."""

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("expires_at must include a timezone")
    return parsed


def _is_expired(value: str) -> bool:
    """Return whether a delegated tool session has reached its expiration."""

    return _expiration(value) <= datetime.now(timezone.utc)


def _credential_fingerprint(token: str, api_url: str) -> str:
    """Identify the credential pair injected into one running server."""

    digest = hashlib.sha256(_CREDENTIAL_FINGERPRINT_DOMAIN)
    digest.update(token.encode())
    digest.update(b"\0")
    digest.update(api_url.encode())
    return digest.hexdigest()


@dataclass(frozen=True)
class _CandidateState:
    username: str
    token: str
    api_url: str
    expires_at: str


def _should_offer_renewal(expires_at: str, *, now: datetime | None = None) -> bool:
    """Return whether a valid candidate is within the explicit-renewal window."""

    current_time = now or datetime.now(timezone.utc)
    remaining = _expiration(expires_at) - current_time
    return timedelta(0) < remaining <= _RENEWAL_PROMPT_THRESHOLD


@dataclass(frozen=True)
class _ExchangeResponse:
    user_id: str
    display_name: str | None
    role: str
    group_ids: tuple[str, ...]
    tool_access_token: str
    expires_at: str
    session_reused: bool = False
    session_renewed: bool = False
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
        allowed = required | {"session_reused", "session_renewed", "notebook_launch_code"}
        if not required.issubset(payload) or not set(payload).issubset(allowed):
            raise ValueError("response omitted required fields")

        user_id = payload["user_id"]
        display_name = payload["display_name"]
        role = payload["role"]
        group_ids = payload["group_ids"]
        tool_access_token = payload["tool_access_token"]
        expires_at = payload["expires_at"]
        session_reused = payload.get("session_reused", False)
        session_renewed = payload.get("session_renewed", False)
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
            or type(session_reused) is not bool
            or type(session_renewed) is not bool
            or session_renewed
            and not session_reused
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
            session_reused=session_reused,
            session_renewed=session_renewed,
            notebook_launch_code=notebook_launch_code,
        )


class DatalabLoginHandler(LoginHandler):
    """Start a browser-bound Datalab authorization from a pending launch."""

    async def get(self) -> None:
        """Render the credential-free fragment-to-POST bootstrap."""

        self.finish(render_fragment_post(self, "datalab_launch_code"))

    async def post(self) -> None:
        """Create the PKCE proof and redirect to the fixed Datalab authorization route."""

        set_launch_page_headers(self)
        self.statsd.incr("login.request")
        code = self.get_body_argument("datalab_launch_code", None) or ""
        if not _HANDOFF_CODE_PATTERN.fullmatch(code):
            raise web.HTTPError(403, _INVALID_LAUNCH_MESSAGE)

        code_verifier = secrets.token_urlsafe(32)
        code_challenge = _pkce_s256_challenge(code_verifier)
        cookie_path, secure = self._pkce_cookie_scope()
        self._set_cookie(
            _PKCE_COOKIE_NAME,
            code_verifier,
            encrypted=True,
            domain=None,
            path=cookie_path,
            httponly=True,
            samesite="Lax",
            secure=secure,
            expires_days=_PKCE_COOKIE_MAX_AGE_DAYS,
        )
        fragment = urlencode(
            {
                "datalab_launch_code": code,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        self.redirect(
            f"{self.authenticator.browser_authorization_url}#{fragment}",
            status=303,
        )

    def _pkce_cookie_scope(self) -> tuple[str, bool]:
        """Return the fixed callback path and Secure value from trusted Hub configuration."""
        configured_public_url = self.settings.get("public_url")
        public_url_value = (
            configured_public_url.geturl()
            if hasattr(configured_public_url, "geturl")
            else str(configured_public_url or "")
        )
        try:
            public_url = validate_http_base_url(
                public_url_value,
                name="JupyterHub.public_url",
                allow_insecure=allow_insecure_http_from_environment(),
            )
        except ValueError as exc:
            raise web.HTTPError(500, "JupyterHub public URL is not safely configured") from exc
        cookie_path = url_path_join(self.hub.base_url, "datalab-callback")
        return cookie_path, urlsplit(public_url).scheme == "https"

    def _read_pkce_verifier(self) -> str:
        """Read and validate the short-lived signed verifier cookie."""
        value = self.get_secure_cookie(
            _PKCE_COOKIE_NAME,
            max_age_days=_PKCE_COOKIE_MAX_AGE_DAYS,
        )
        try:
            code_verifier = value.decode("ascii") if isinstance(value, bytes) else value
        except UnicodeDecodeError as exc:
            raise web.HTTPError(403, _INVALID_LAUNCH_MESSAGE) from exc
        if not isinstance(code_verifier, str) or not _PKCE_VERIFIER_PATTERN.fullmatch(
            code_verifier
        ):
            raise web.HTTPError(403, _INVALID_LAUNCH_MESSAGE)
        return code_verifier

    def _clear_pkce_verifier(self) -> None:
        cookie_path, secure = self._pkce_cookie_scope()
        self.clear_cookie(
            _PKCE_COOKIE_NAME,
            domain=None,
            path=cookie_path,
            httponly=True,
            samesite="Lax",
            secure=secure,
        )

    async def _callback_post(self) -> None:
        """Complete a PKCE-bound authorization submitted by the callback bootstrap."""

        set_launch_page_headers(self)
        self.statsd.incr("login.request")
        authorization_code = self.get_body_argument("datalab_authorization_code", None) or ""
        if not _HANDOFF_CODE_PATTERN.fullmatch(authorization_code):
            raise web.HTTPError(403, _INVALID_LAUNCH_MESSAGE)
        code_verifier = self._read_pkce_verifier()
        expected_user_id = await self._existing_subject()

        renewal_choice = self.get_body_argument("renew_existing_session", None)
        renewal_binding = self.get_body_argument("renewal_binding", None)
        if renewal_choice is None and renewal_binding is None:
            candidate = await self._candidate_state()
            if candidate is not None and _should_offer_renewal(candidate.expires_at):
                binding = self._create_confirmation_binding(authorization_code, candidate)
                self.finish(render_renewal_confirmation(self, authorization_code, binding))
                return
            await self._authenticate_and_redirect(
                authorization_code,
                code_verifier=code_verifier,
                expected_user_id=expected_user_id,
                renew_existing_session=False,
            )
            return

        if renewal_choice not in {"true", "false"} or not renewal_binding:
            raise web.HTTPError(400, "Invalid Datalab access extension choice")
        await self._authenticate_and_redirect(
            authorization_code,
            code_verifier=code_verifier,
            expected_user_id=expected_user_id,
            renew_existing_session=renewal_choice == "true",
            confirmation_binding=renewal_binding,
        )

    async def _authenticate_and_redirect(
        self,
        authorization_code: str,
        *,
        code_verifier: str,
        expected_user_id: str | None,
        renew_existing_session: bool,
        confirmation_binding: str | None = None,
    ) -> None:
        """Exchange one authorization, reconcile the user server, and complete the handoff."""

        self.set_header("Cache-Control", "no-store")
        self.set_header("Referrer-Policy", "no-referrer")
        admission_token = self.authenticator._try_admit_exchange()
        if admission_token is None:
            self.statsd.incr("login.exchange_rejected")
            self.set_header("Retry-After", str(_EXCHANGE_RETRY_AFTER_SECONDS))
            raise web.HTTPError(503, _BUSY_LAUNCH_MESSAGE)

        lock_acquired = False
        try:
            try:
                async with asyncio.timeout(_EXCHANGE_LOCK_WAIT_SECONDS):
                    await self.authenticator.exchange_lock.acquire()
                lock_acquired = True
            except TimeoutError as exc:
                self.statsd.incr("login.exchange_wait_timeout")
                self.set_header("Retry-After", str(_EXCHANGE_RETRY_AFTER_SECONDS))
                raise web.HTTPError(503, _BUSY_LAUNCH_MESSAGE) from exc

            self._datalab_authorization_code = authorization_code
            self._datalab_code_verifier = code_verifier
            self._datalab_expected_user_id = expected_user_id
            try:
                candidate = await self._candidate_state()
                if confirmation_binding is not None:
                    self._require_confirmation_binding(
                        authorization_code,
                        candidate,
                        confirmation_binding,
                    )
                self._datalab_candidate_token = candidate.token if candidate is not None else None
                self._datalab_renew_existing_session = renew_existing_session
                user = await self.login_user()
            finally:
                for attribute in (
                    "_datalab_authorization_code",
                    "_datalab_code_verifier",
                    "_datalab_expected_user_id",
                    "_datalab_candidate_token",
                    "_datalab_renew_existing_session",
                ):
                    if hasattr(self, attribute):
                        delattr(self, attribute)
        finally:
            if lock_acquired:
                self.authenticator.exchange_lock.release()
            self.authenticator._release_exchange(admission_token)
        if user is None:
            raise web.HTTPError(403, _INVALID_LAUNCH_MESSAGE)

        exchange = self._datalab_exchange
        self._clear_pkce_verifier()
        notebook_launch_code = exchange.notebook_launch_code
        lifecycle_lock = self._lifecycle_lock(user)
        async with lifecycle_lock:
            await self._reconcile_server(
                user,
                token=exchange.tool_access_token,
                api_url=self.authenticator.api_url.rstrip("/"),
                session_reused=exchange.session_reused,
            )
            self._require_matching_server(
                user,
                token=exchange.tool_access_token,
                api_url=self.authenticator.api_url.rstrip("/"),
            )
            await self._require_current_credentials(
                user,
                token=exchange.tool_access_token,
                api_url=self.authenticator.api_url.rstrip("/"),
            )
            if notebook_launch_code is None:
                next_url = workspace_url(user.url)
            else:
                next_url = (
                    f"{url_path_join(user.url, 'datalab/open-selected')}#"
                    f"{urlencode({'notebook_launch_code': notebook_launch_code})}"
                )
            self.redirect(next_url, status=303)

    async def _existing_subject(self) -> str | None:
        """Recover and cross-check the immutable subject of an existing Hub login."""
        user = self.current_user
        if user is None:
            return None
        try:
            auth_state = await user.get_auth_state()
            current_user = auth_state.get("current_user") if auth_state else None
            user_id = current_user.get("id") if isinstance(current_user, dict) else None
            if (
                not isinstance(user_id, str)
                or not _USER_ID_PATTERN.fullmatch(user_id)
                or user.name != f"datalab-{user_id}"
            ):
                raise web.HTTPError(
                    409,
                    "The existing JupyterHub identity is inconsistent; log out and launch again",
                )
            return user_id
        except web.HTTPError:
            raise
        except Exception as exc:
            self.log.warning("Unable to verify the existing JupyterHub identity: %s", exc)
            raise web.HTTPError(
                409,
                "The existing JupyterHub identity cannot be verified; log out and launch again",
            ) from exc

    async def _candidate_state(self) -> _CandidateState | None:
        """Read a reusable credential only from the authenticated Hub user's state."""

        user = self.current_user
        if user is None:
            return None
        try:
            auth_state = await user.get_auth_state()
            username = user.name
            token = auth_state.get("tool_access_token") if auth_state else None
            api_url = auth_state.get("api_url") if auth_state else None
            expires_at = auth_state.get("expires_at") if auth_state else None
            if (
                not isinstance(username, str)
                or not username
                or not isinstance(token, str)
                or not token
                or api_url != self.authenticator.api_url.rstrip("/")
                or not isinstance(expires_at, str)
                or _is_expired(expires_at)
            ):
                return None
            return _CandidateState(username, token, api_url, expires_at)
        except (AttributeError, KeyError, TypeError, ValueError):
            return None
        except Exception as exc:
            self.log.warning("Unable to read existing datalab authentication state: %s", exc)
            return None

    def _cookie_secret(self) -> bytes:
        secret = self.settings["cookie_secret"]
        return secret.encode() if isinstance(secret, str) else secret

    def _confirmation_mac(self, code: str, candidate: _CandidateState) -> bytes:
        payload = json.dumps(
            [
                candidate.username,
                code,
                candidate.token,
                candidate.api_url,
                candidate.expires_at,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        return hmac.digest(self._cookie_secret(), _CONFIRMATION_DOMAIN + payload, "sha256")

    def _create_confirmation_binding(self, code: str, candidate: _CandidateState) -> str:
        signed = web.create_signed_value(
            self._cookie_secret(),
            _CONFIRMATION_NAME,
            self._confirmation_mac(code, candidate),
        )
        return signed.decode()

    def _require_confirmation_binding(
        self,
        code: str,
        candidate: _CandidateState | None,
        binding: str,
    ) -> None:
        if candidate is None:
            raise web.HTTPError(409, "Datalab access changed; launch JupyterLab again")
        signed_mac = web.decode_signed_value(
            self._cookie_secret(),
            _CONFIRMATION_NAME,
            binding,
            max_age_days=_CONFIRMATION_MAX_AGE_DAYS,
        )
        expected_mac = self._confirmation_mac(code, candidate)
        if signed_mac is None or not hmac.compare_digest(signed_mac, expected_mac):
            raise web.HTTPError(409, "Datalab access changed; launch JupyterLab again")

    @staticmethod
    def _lifecycle_lock(user: Any) -> asyncio.Lock:
        lock = getattr(user, "_datalab_lifecycle_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            user._datalab_lifecycle_lock = lock
        return lock

    @staticmethod
    async def _require_current_credentials(user: Any, *, token: str, api_url: str) -> None:
        try:
            auth_state = await user.get_auth_state()
        except Exception as exc:
            raise web.HTTPError(503, "Unable to verify current Jupyter credentials") from exc
        if not auth_state or (
            auth_state.get("tool_access_token"),
            auth_state.get("api_url"),
        ) != (token, api_url):
            raise web.HTTPError(409, "A newer Jupyter launch superseded this request")

    async def _reconcile_server(
        self,
        user: Any,
        *,
        token: str,
        api_url: str,
        session_reused: bool,
    ) -> None:
        """Retain a compatible server or perform one controlled replacement."""

        await self._require_current_credentials(user, token=token, api_url=api_url)
        spawner = user.spawners.get("")
        if spawner is None:
            await self._spawn_current_server(user, token=token, api_url=api_url)
            return

        pending = spawner.pending
        if pending == "spawn":
            await self._await_server_future(
                spawner._spawn_future,
                "The existing Jupyter server did not finish starting",
                timeout=self.slow_spawn_timeout,
            )
            if not spawner.ready:
                raise web.HTTPError(503, "The existing Jupyter server did not become ready")
            await self._require_current_credentials(user, token=token, api_url=api_url)
            spawner = user.spawners.get("")
            if spawner is None:
                raise web.HTTPError(503, "The existing Jupyter server disappeared")
        elif pending == "stop":
            await self._await_server_future(
                spawner._stop_future,
                "The existing Jupyter server did not finish stopping",
                timeout=self.slow_stop_timeout,
            )
            await self._verify_server_stopped(spawner)
            self._clear_server_provenance(spawner)
            await self._require_current_credentials(user, token=token, api_url=api_url)
            await self._spawn_current_server(user, token=token, api_url=api_url)
            return
        elif pending:
            raise web.HTTPError(503, "The existing Jupyter server is busy")

        if spawner.ready:
            fingerprint = getattr(spawner, _CREDENTIAL_FINGERPRINT_ATTRIBUTE, None)
            if session_reused and fingerprint == _credential_fingerprint(token, api_url):
                return
            try:
                stop_future = await self.stop_single_user(user)
            except Exception as exc:
                raise web.HTTPError(503, "Unable to stop the existing Jupyter server") from exc
            await self._await_server_future(
                stop_future,
                "The existing Jupyter server did not finish stopping",
                timeout=self.slow_stop_timeout,
            )
            await self._verify_server_stopped(spawner)
            self._clear_server_provenance(spawner)
            await self._require_current_credentials(user, token=token, api_url=api_url)
            await self._spawn_current_server(user, token=token, api_url=api_url)
            return

        if spawner.active:
            raise web.HTTPError(503, "The existing Jupyter server is in an unknown state")
        await self._spawn_current_server(user, token=token, api_url=api_url)

    @staticmethod
    def _require_matching_server(user: Any, *, token: str, api_url: str) -> None:
        """Require a ready default server with the expected credential provenance."""

        spawner = user.spawners.get("")
        if spawner is None or spawner.pending or not spawner.ready:
            raise web.HTTPError(503, "The Jupyter server is not ready")
        if getattr(spawner, _CREDENTIAL_FINGERPRINT_ATTRIBUTE, None) != _credential_fingerprint(
            token, api_url
        ):
            raise web.HTTPError(503, "The Jupyter server credentials could not be verified")

    async def _spawn_current_server(self, user: Any, *, token: str, api_url: str) -> None:
        await self._require_current_credentials(user, token=token, api_url=api_url)
        # Hub admin/API actions do not acquire our per-user lock, so the default
        # Spawner may have changed while an earlier stop operation was awaited.
        current_spawner = user.spawners.get("")
        if current_spawner is not None and (
            current_spawner.pending or current_spawner.ready or current_spawner.active
        ):
            raise web.HTTPError(503, "A Jupyter server appeared while preparing the replacement")
        try:
            await self.spawn_single_user(user)
            replacement_spawner = user.spawners[""]
            if replacement_spawner._spawn_future is not None:
                await self._await_server_future(
                    replacement_spawner._spawn_future,
                    "The fresh Jupyter server did not finish starting",
                    timeout=self.slow_spawn_timeout,
                )
        except Exception as exc:
            raise web.HTTPError(503, "Unable to start the fresh Jupyter server") from exc
        if not replacement_spawner.ready:
            raise web.HTTPError(503, "The fresh Jupyter server did not become ready")
        await self._require_current_credentials(user, token=token, api_url=api_url)

    @staticmethod
    def _clear_server_provenance(spawner: Any) -> None:
        if hasattr(spawner, _CREDENTIAL_FINGERPRINT_ATTRIBUTE):
            delattr(spawner, _CREDENTIAL_FINGERPRINT_ATTRIBUTE)

    @staticmethod
    async def _await_server_future(future: Any, failure_message: str, *, timeout: float) -> None:
        """Await a Hub lifecycle future and normalize failures for the launch flow."""

        if future is None:
            raise web.HTTPError(503, failure_message)
        try:
            # The Spawner owns this shared future. Shielding lets this request
            # time out or be cancelled without cancelling the server operation.
            await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
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


class DatalabCallbackHandler(DatalabLoginHandler):
    """Complete a browser-authorized launch without exposing fragment credentials."""

    async def get(self) -> None:
        """Render the credential-free authorization-code fragment bootstrap."""
        self.finish(render_fragment_post(self, "datalab_authorization_code"))

    async def post(self) -> None:
        await self._callback_post()


class DatalabAuthenticator(Authenticator):
    """Exchange a browser-authorized Datalab code for a tool access token."""

    api_url = Unicode(config=True, help="Internal datalab API URL.")
    browser_api_url = Unicode(config=True, help="Trusted browser-facing datalab API base URL.")
    client_id = Unicode(config=True, help="JupyterHub integration client identifier.")
    client_secret = Unicode(config=True, help="JupyterHub integration client secret.")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if not self.browser_api_url:
            raise ValueError("DATALAB_JUPYTER_BROWSER_API_URL must be configured")
        self.exchange_lock = asyncio.Lock()
        self._exchange_admission_tokens: set[object] = set()

    def _try_admit_exchange(self) -> object | None:
        """Reserve one bounded Hub-local exchange slot without waiting."""

        if len(self._exchange_admission_tokens) >= _MAX_ADMITTED_EXCHANGES:
            return None
        token = object()
        self._exchange_admission_tokens.add(token)
        return token

    def _release_exchange(self, token: object) -> None:
        """Release a previously admitted exchange slot."""

        self._exchange_admission_tokens.discard(token)

    @validate("browser_api_url")
    def _validate_browser_api_url(self, proposal: dict[str, Any]) -> str:
        return validate_http_base_url(
            proposal["value"],
            name="DATALAB_JUPYTER_BROWSER_API_URL",
            allow_insecure=allow_insecure_http_from_environment(),
        )

    @property
    def browser_authorization_url(self) -> str:
        return f"{self.browser_api_url.rstrip('/')}{BROWSER_AUTHORIZATION_PATH}"

    def login_url(self, base_url: str) -> str:
        """Return the handler that always performs launch-code authentication."""

        return url_path_join(base_url, "datalab-login")

    def get_handlers(self, app: Any) -> list[tuple[str, type[DatalabLoginHandler]]]:
        """Register the initial and callback handlers below the Hub base URL."""

        public_url = getattr(app, "public_url", "")
        validate_http_base_url(
            public_url.rstrip("/"),
            name="JupyterHub.public_url",
            allow_insecure=allow_insecure_http_from_environment(),
        )

        return [
            ("/datalab-login", DatalabLoginHandler),
            ("/datalab-callback", DatalabCallbackHandler),
        ]

    async def authenticate(
        self, handler: Any, data: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        """Exchange an authorized code for Datalab identity and a delegated tool session."""

        authorization_code = getattr(handler, "_datalab_authorization_code", None) or ""
        code_verifier = getattr(handler, "_datalab_code_verifier", None) or ""
        expected_user_id = getattr(handler, "_datalab_expected_user_id", None)
        if (
            not isinstance(authorization_code, str)
            or not _HANDOFF_CODE_PATTERN.fullmatch(authorization_code)
            or not isinstance(code_verifier, str)
            or not _PKCE_VERIFIER_PATTERN.fullmatch(code_verifier)
            or expected_user_id is not None
            and (
                not isinstance(expected_user_id, str)
                or not _USER_ID_PATTERN.fullmatch(expected_user_id)
            )
        ):
            return None

        exchange_url = f"{self.api_url.rstrip('/')}/{EXCHANGE_PATH.lstrip('/')}"
        candidate = getattr(handler, "_datalab_candidate_token", None)
        renew_existing_session = getattr(handler, "_datalab_renew_existing_session", False)
        headers = {REUSE_CANDIDATE_HEADER: candidate} if candidate else None
        request_payload: dict[str, str | bool] = {
            "authorization_code": authorization_code,
            "code_verifier": code_verifier,
            "renew_existing_session": renew_existing_session,
        }
        if expected_user_id is not None:
            request_payload["expected_user_id"] = expected_user_id
        try:
            async with httpx.AsyncClient(timeout=_EXCHANGE_TIMEOUT_SECONDS) as client:
                # HTTPX applies its timeout to individual I/O operations. The
                # outer deadline also bounds a response that keeps trickling data.
                async with asyncio.timeout(_EXCHANGE_TIMEOUT_SECONDS):
                    response = await client.post(
                        exchange_url,
                        json=request_payload,
                        auth=httpx.BasicAuth(self.client_id, self.client_secret),
                        headers=headers,
                    )
        except (httpx.RequestError, TimeoutError) as exc:
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
        if exchange.session_reused and exchange.tool_access_token != candidate:
            self.log.error("datalab claimed reuse with a different delegated token")
            raise web.HTTPError(502, "datalab returned invalid authentication data")
        if expected_user_id is not None and exchange.user_id != expected_user_id:
            self.log.error("datalab returned a different user than the existing Hub subject")
            raise web.HTTPError(502, "datalab returned invalid authentication data")

        handler._datalab_exchange = exchange

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
        return {
            "name": username,
            "admin": False,
            "auth_state": auth_state,
        }

    async def pre_spawn_start(self, user: Any, spawner: Any) -> None:
        """Inject only delegated datalab state into this single-user server."""

        auth_state = await user.get_auth_state()
        token = auth_state.get("tool_access_token") if auth_state else None
        api_url = auth_state.get("api_url") if auth_state else None
        current_user = auth_state.get("current_user") if auth_state else None
        expires_at = auth_state.get("expires_at") if auth_state else None
        if (
            not isinstance(token, str)
            or not token
            or not isinstance(api_url, str)
            or not api_url
            or not isinstance(current_user, dict)
            or not isinstance(expires_at, str)
        ):
            raise web.HTTPError(
                403, "The delegated tool session is invalid; launch JupyterLab again"
            )
        try:
            expired = _is_expired(expires_at)
            current_user_json = json.dumps(current_user, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise web.HTTPError(403, "The delegated tool session is invalid; launch again") from exc
        if expired:
            raise web.HTTPError(
                403, "The delegated tool session has expired; launch JupyterLab again"
            )

        spawner.environment.update(
            {
                "DATALAB_API_KEY": token,
                "DATALAB_API_URL": api_url,
                "DATALAB_CURRENT_USER_JSON": current_user_json,
            }
        )
        setattr(
            spawner,
            _CREDENTIAL_FINGERPRINT_ATTRIBUTE,
            _credential_fingerprint(
                token,
                api_url,
            ),
        )

    async def post_spawn_stop(self, user: Any, spawner: Any) -> None:
        """Forget credential provenance when a single-user server stops."""

        if hasattr(spawner, _CREDENTIAL_FINGERPRINT_ATTRIBUTE):
            delattr(spawner, _CREDENTIAL_FINGERPRINT_ATTRIBUTE)
