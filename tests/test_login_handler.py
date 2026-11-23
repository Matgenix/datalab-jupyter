import asyncio
import logging
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from tornado import web
from tornado.httputil import HTTPHeaders, HTTPServerRequest, parse_body_arguments

from datalab_jupyter.hub.authenticator import (
    DatalabAuthenticator,
    DatalabLoginHandler,
    _credential_fingerprint,
)


class _LifecycleCompletion:
    def __init__(
        self,
        spawner,
        event,
        ready_after,
        error=None,
        on_complete=None,
    ):
        self.spawner = spawner
        self.event = event
        self.ready_after = ready_after
        self.error = error
        self.on_complete = on_complete

    def __await__(self):
        self.spawner.events.append(f"{self.event}-complete")
        self.spawner.pending = None
        self.spawner.ready = self.ready_after
        self.spawner.running = self.ready_after
        if self.on_complete is not None:
            self.on_complete()
        if self.error is not None:
            raise self.error
        if False:
            yield None
        return None


class _Spawner:
    def __init__(self, *, ready=False, pending=None, events=None):
        self.ready = ready
        self.pending = pending
        self.events = events if events is not None else []
        self._spawn_future = None
        self._stop_future = None
        self.environment = {"DATALAB_API_KEY": "stale-token"}
        self.running = ready or pending == "stop"
        if pending == "spawn":
            self._spawn_future = _LifecycleCompletion(self, "pending-spawn", True)
        elif pending == "stop":
            self._stop_future = _LifecycleCompletion(self, "pending-stop", False)

    @property
    def active(self):
        return bool(self.pending or self.ready)

    async def poll(self):
        return None if self.running else 0


class _User:
    url = "/user/test/"

    def __init__(self, spawner):
        self.spawners = {"": spawner}
        self.auth_state = {
            "tool_access_token": "stale-token",
            "api_url": "http://datalab-api:5001",
            "current_user": {"id": "user-id", "username": "datalab-user-id"},
            "expires_at": "2099-01-01T00:00:00+00:00",
        }

    async def get_auth_state(self):
        return self.auth_state


class _Handler:
    xsrf_token = b"xsrf-token"
    slow_spawn_timeout = 10
    slow_stop_timeout = 10

    def __init__(
        self,
        code="secret",
        *,
        ready=False,
        pending=None,
        selected_items=True,
        stop_ready_after=False,
        spawn_ready_after=True,
        spawn_error=None,
        session_reused=False,
        returned_token="fresh-token",  # noqa: S107
        known_provenance=True,
        login_error=None,
        login_result=True,
    ):
        self.request = SimpleNamespace(
            path="/hub/datalab-login",
            uri="/hub/datalab-login",
        )
        self.statsd = SimpleNamespace(incr=lambda _name: None)
        self.headers = {}
        self.finished = None
        self.redirected_to = None
        self.code = code
        self.login_calls = 0
        self.events = []
        self.stop_ready_after = stop_ready_after
        self.spawn_ready_after = spawn_ready_after
        self.spawn_error = spawn_error
        self.session_reused = session_reused
        self.returned_token = returned_token
        self.selected_items = selected_items
        self.login_error = login_error
        self.login_result = login_result
        self.spawn_environments = []
        self.spawner = _Spawner(ready=ready, pending=pending, events=self.events)
        self.user = _User(self.spawner)
        self.current_user = self.user
        self.authenticator = SimpleNamespace(
            api_url="http://datalab-api:5001",
            exchange_lock=asyncio.Lock(),
        )
        self.log = SimpleNamespace(warning=lambda *_args: None)
        if ready and known_provenance:
            self.spawner._datalab_credential_fingerprint = _credential_fingerprint(
                "stale-token", self.authenticator.api_url
            )
        if pending == "stop":
            self.spawner._stop_future.on_complete = lambda: self.user.spawners.pop("", None)

    def get_body_argument(self, name, default):
        return self.code if name == "datalab_launch_code" else default

    def set_header(self, name, value):
        self.headers[name] = value

    def finish(self, value):
        self.finished = value

    async def login_user(self):
        self.login_calls += 1
        self.events.append("login")
        if self.login_error is not None:
            raise self.login_error
        if not self.login_result:
            return None
        self._datalab_exchange = SimpleNamespace(
            tool_access_token=self.returned_token,
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            session_reused=self.session_reused,
            notebook_launch_code="selected-items-code" if self.selected_items else None,
        )
        self.user.auth_state = {
            "tool_access_token": self.returned_token,
            "api_url": self.authenticator.api_url,
            "current_user": {"id": "user-id", "username": "datalab-user-id"},
            "expires_at": self._datalab_exchange.expires_at,
        }
        return self.user

    async def _authenticate_and_redirect(self, code):
        await DatalabLoginHandler._authenticate_and_redirect(self, code)

    async def _candidate_token(self):
        return await DatalabLoginHandler._candidate_token(self)

    def _lifecycle_lock(self, user):
        return DatalabLoginHandler._lifecycle_lock(user)

    async def _require_current_credentials(self, user, *, token, api_url):
        await DatalabLoginHandler._require_current_credentials(user, token=token, api_url=api_url)

    async def _reconcile_server(self, user, *, token, api_url, session_reused, required):
        await DatalabLoginHandler._reconcile_server(
            self,
            user,
            token=token,
            api_url=api_url,
            session_reused=session_reused,
            required=required,
        )

    async def _spawn_current_server(self, user, *, token, api_url):
        await DatalabLoginHandler._spawn_current_server(self, user, token=token, api_url=api_url)

    def _clear_server_provenance(self, spawner):
        DatalabLoginHandler._clear_server_provenance(spawner)

    async def _await_server_future(self, future, failure_message, *, timeout):
        await DatalabLoginHandler._await_server_future(
            future,
            failure_message,
            timeout=timeout,
        )

    async def _verify_server_stopped(self, spawner):
        await DatalabLoginHandler._verify_server_stopped(spawner)

    def get_next_url(self, _user):
        return "/hub/home"

    async def stop_single_user(self, _user):
        self.events.append("stop")
        self.spawner.pending = "stop"
        self.spawner.ready = False
        self.spawner._stop_future = _LifecycleCompletion(
            self.spawner,
            "stop",
            self.stop_ready_after,
            on_complete=lambda: self.user.spawners.pop("", None),
        )
        return self.spawner._stop_future

    async def spawn_single_user(self, user):
        self.events.append("spawn")
        if "" not in user.spawners:
            self.spawner = _Spawner(events=self.events)
            user.spawners[""] = self.spawner
        await DatalabAuthenticator.pre_spawn_start(None, user, self.spawner)
        self.spawn_environments.append(dict(self.spawner.environment))
        self.spawner.pending = "spawn"
        self.spawner.ready = False
        self.spawner._spawn_future = _LifecycleCompletion(
            self.spawner,
            "spawn",
            self.spawn_ready_after,
            self.spawn_error,
        )

    def redirect(self, url, permanent):
        self.events.append("redirect")
        self.redirected_to = (url, permanent)


class LoginHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_renders_bootstrap_without_attempting_login(self):
        handler = _Handler()

        await DatalabLoginHandler.get(handler)

        self.assertIn('name="datalab_launch_code"', handler.finished)
        self.assertEqual(handler.login_calls, 0)
        self.assertEqual(handler.headers["Cache-Control"], "no-store")

    async def test_post_does_not_fall_back_to_a_query_code(self):
        handler = _Handler(code=None)
        handler.request.uri += "?datalab_launch_code=query-secret"

        with self.assertRaises(web.HTTPError) as raised:
            await DatalabLoginHandler.post(handler)

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(handler.login_calls, 0)

    async def test_ready_server_is_replaced_before_selected_items_redirect(self):
        handler = _Handler(ready=True)
        old_spawner = handler.spawner

        await DatalabLoginHandler.post(handler)

        self.assertEqual(
            handler.events,
            ["login", "stop", "stop-complete", "spawn", "spawn-complete", "redirect"],
        )
        self.assertEqual(handler.spawn_environments[0]["DATALAB_API_KEY"], "fresh-token")
        self.assertIsNot(handler.spawner, old_spawner)
        self.assertEqual(
            handler.redirected_to,
            (
                "/user/test/datalab/open-selected#notebook_launch_code=selected-items-code",
                False,
            ),
        )
        self.assertNotIn("?notebook_launch_code=", handler.redirected_to[0])
        self.assertFalse(hasattr(handler, "_datalab_launch_code"))
        self.assertNotIn("notebook_launch_code", handler.user.auth_state)

    async def test_ready_server_with_matching_reused_credentials_is_retained(self):
        handler = _Handler(
            ready=True,
            session_reused=True,
            returned_token="stale-token",  # noqa: S106
        )
        original_spawner = handler.spawner

        await DatalabLoginHandler.post(handler)

        self.assertEqual(handler.events, ["login", "redirect"])
        self.assertIs(handler.user.spawners[""], original_spawner)

    async def test_fresh_selected_items_launch_spawns_without_a_stop(self):
        handler = _Handler()

        await DatalabLoginHandler.post(handler)

        self.assertEqual(handler.events, ["login", "spawn", "spawn-complete", "redirect"])

    async def test_fresh_normal_launch_keeps_the_standard_hub_redirect(self):
        handler = _Handler(selected_items=False)

        await DatalabLoginHandler.post(handler)

        self.assertEqual(handler.events, ["login", "redirect"])
        self.assertEqual(handler.redirected_to, ("/hub/home", False))

    async def test_pending_spawn_is_awaited_then_replaced(self):
        handler = _Handler(pending="spawn")

        await DatalabLoginHandler.post(handler)

        self.assertEqual(
            handler.events,
            [
                "login",
                "pending-spawn-complete",
                "stop",
                "stop-complete",
                "spawn",
                "spawn-complete",
                "redirect",
            ],
        )

    async def test_pending_stop_is_awaited_before_the_replacement_spawn(self):
        handler = _Handler(pending="stop")

        await DatalabLoginHandler.post(handler)

        self.assertEqual(
            handler.events,
            ["login", "pending-stop-complete", "spawn", "spawn-complete", "redirect"],
        )

    async def test_pending_stop_does_not_spawn_over_a_new_server(self):
        handler = _Handler(pending="stop")
        new_spawner = _Spawner(ready=True, events=handler.events)
        handler.spawner._stop_future.on_complete = lambda: handler.user.spawners.__setitem__(
            "", new_spawner
        )

        with self.assertRaises(web.HTTPError) as raised:
            await DatalabLoginHandler.post(handler)

        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn("spawn", handler.events)
        self.assertIs(handler.user.spawners[""], new_spawner)

    async def test_pending_stop_for_an_ordinary_launch_uses_standard_redirect(self):
        handler = _Handler(pending="stop", selected_items=False)

        await DatalabLoginHandler.post(handler)

        self.assertEqual(handler.events, ["login", "pending-stop-complete", "redirect"])

    async def test_unknown_provenance_replaces_a_ready_server_once(self):
        handler = _Handler(
            ready=True,
            session_reused=True,
            returned_token="stale-token",  # noqa: S106
            known_provenance=False,
        )

        await DatalabLoginHandler.post(handler)

        self.assertEqual(handler.events.count("stop"), 1)
        self.assertEqual(handler.events.count("spawn"), 1)

    async def test_candidate_comes_only_from_valid_current_auth_state(self):
        handler = _Handler()

        self.assertEqual(await handler._candidate_token(), "stale-token")
        handler.user.auth_state["expires_at"] = "2000-01-01T00:00:00+00:00"
        self.assertIsNone(await handler._candidate_token())
        handler.current_user = None
        self.assertIsNone(await handler._candidate_token())

    async def test_failed_exchange_does_not_stop_a_ready_server_and_releases_gate(self):
        handler = _Handler(ready=True, login_result=False)

        with self.assertRaises(web.HTTPError) as raised:
            await DatalabLoginHandler.post(handler)

        self.assertEqual(raised.exception.status_code, 403)
        self.assertNotIn("stop", handler.events)
        self.assertFalse(handler.authenticator.exchange_lock.locked())

    async def test_exchange_gate_releases_when_login_is_cancelled(self):
        handler = _Handler(login_error=asyncio.CancelledError())

        with self.assertRaises(asyncio.CancelledError):
            await DatalabLoginHandler.post(handler)

        self.assertFalse(handler.authenticator.exchange_lock.locked())
        self.assertFalse(hasattr(handler, "_datalab_launch_code"))

    async def test_superseded_credentials_fail_after_pending_spawn(self):
        handler = _Handler(pending="spawn")
        handler.spawner._spawn_future.on_complete = lambda: handler.user.auth_state.update(
            tool_access_token="newer-token"  # noqa: S106
        )

        with self.assertRaises(web.HTTPError) as raised:
            await DatalabLoginHandler.post(handler)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertNotIn("stop", handler.events)
        self.assertIsNone(handler.redirected_to)

    async def test_same_token_concurrent_launches_retain_one_server(self):
        first = _Handler(
            ready=True,
            session_reused=True,
            returned_token="stale-token",  # noqa: S106
        )
        second = _Handler(
            ready=True,
            session_reused=True,
            returned_token="stale-token",  # noqa: S106
        )
        second.user = first.user
        second.current_user = first.user
        second.spawner = first.spawner
        second.authenticator = first.authenticator

        await asyncio.gather(DatalabLoginHandler.post(first), DatalabLoginHandler.post(second))

        self.assertNotIn("stop", first.events)
        self.assertNotIn("stop", second.events)
        self.assertIs(first.user.spawners[""], first.spawner)

    async def test_lifecycle_locks_are_per_user(self):
        first = _Handler()
        second = _Handler()

        self.assertIs(first._lifecycle_lock(first.user), first._lifecycle_lock(first.user))
        self.assertIsNot(first._lifecycle_lock(first.user), second._lifecycle_lock(second.user))

    async def test_lifecycle_wait_is_bounded_without_cancelling_the_shared_future(self):
        release = asyncio.Event()
        shared_future = asyncio.create_task(release.wait())

        with self.assertRaises(web.HTTPError) as raised:
            await DatalabLoginHandler._await_server_future(
                shared_future,
                "The Jupyter server operation timed out",
                timeout=0.01,
            )

        self.assertEqual(raised.exception.status_code, 503)
        self.assertFalse(shared_future.done())
        release.set()
        await shared_future

        release = asyncio.Event()
        shared_future = asyncio.create_task(release.wait())
        waiter = asyncio.create_task(
            DatalabLoginHandler._await_server_future(
                shared_future,
                "The Jupyter server operation was cancelled",
                timeout=10,
            )
        )
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertFalse(shared_future.done())
        release.set()
        await shared_future

    async def test_stop_failure_returns_503_without_redirecting(self):
        handler = _Handler(ready=True, stop_ready_after=True)

        with self.assertRaises(web.HTTPError) as raised:
            await DatalabLoginHandler.post(handler)

        self.assertEqual(raised.exception.status_code, 503)
        self.assertIsNone(handler.redirected_to)

    async def test_replacement_spawn_failures_return_503_without_redirecting(self):
        cases = (
            {"spawn_ready_after": False},
            {"spawn_error": RuntimeError("spawn failed")},
        )
        for options in cases:
            with self.subTest(options=options):
                handler = _Handler(ready=True, **options)

                with self.assertRaises(web.HTTPError) as raised:
                    await DatalabLoginHandler.post(handler)

                self.assertEqual(raised.exception.status_code, 503)
                self.assertIsNone(handler.redirected_to)


class _PreparedLoginHandler(DatalabLoginHandler):
    """Exercise JupyterHub's real prepare-before-post handler lifecycle."""

    async def get_current_user(self):
        self._jupyterhub_user = self.settings["existing_user"]
        return self._jupyterhub_user

    def _resolve_roles_and_scopes(self):
        self.expanded_scopes = set()
        self.parsed_scopes = {}

    async def login_user(self, data=None):
        self.settings["candidate_seen"] = self._datalab_candidate_token
        target_user = self.settings["target_user"]
        target_user.auth_state = {
            "tool_access_token": "target-token",
            "api_url": self.authenticator.api_url,
            "current_user": {"id": "target-user"},
            "expires_at": "2099-01-01T00:00:00+00:00",
        }
        self._datalab_exchange = SimpleNamespace(
            tool_access_token="target-token",  # noqa: S106
            session_reused=False,
            notebook_launch_code=None,
        )
        return target_user

    def get_next_url(self, user=None):
        return "/hub/home"

    def redirect(self, url, permanent=False, status=None):
        self.redirected_to = (url, permanent)


class PreparedHandlerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_exposes_cookie_user_before_target_auth_state_is_persisted(self):
        existing_user = _User(_Spawner())
        target_user = _User(_Spawner())
        target_user.spawners = {}
        application = web.Application(
            authenticator=SimpleNamespace(
                api_url="http://datalab-api:5001",
                exchange_lock=asyncio.Lock(),
            ),
            existing_user=existing_user,
            target_user=target_user,
            statsd=SimpleNamespace(incr=lambda _name: None),
            hub=SimpleNamespace(base_url="/hub/"),
            log=logging.getLogger(__name__),
            candidate_seen=None,
        )
        headers = HTTPHeaders({"Content-Type": "application/x-www-form-urlencoded"})
        body = b"datalab_launch_code=launch-code"
        request = HTTPServerRequest(
            method="POST",
            uri="/hub/datalab-login",
            headers=headers,
            body=body,
            connection=SimpleNamespace(set_close_callback=lambda _callback: None),
        )
        parse_body_arguments(headers["Content-Type"], body, request.body_arguments, request.files)
        handler = _PreparedLoginHandler(application, request)

        await handler.prepare()
        await handler.post()

        self.assertEqual(application.settings["candidate_seen"], "stale-token")
        self.assertEqual(target_user.auth_state["tool_access_token"], "target-token")
        self.assertEqual(handler.redirected_to, ("/hub/home", False))
