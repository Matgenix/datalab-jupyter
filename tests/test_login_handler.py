import unittest
from types import SimpleNamespace

from tornado import web

from datalab_jupyter.hub.authenticator import DatalabAuthenticator, DatalabLoginHandler


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

    def __init__(self, spawner, selected_items=True):
        self.spawners = {"": spawner}
        self.auth_state = {
            "tool_access_token": "stale-token",
            "api_url": "http://datalab-api:5001",
            "current_user": {"id": "user-id", "username": "datalab-user-id"},
            "expires_at": "2099-01-01T00:00:00+00:00",
        }
        if selected_items:
            self.auth_state["notebook_launch_code"] = "selected-items-code"

    async def get_auth_state(self):
        return self.auth_state


class _Handler:
    xsrf_token = b"xsrf-token"

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
        self.spawn_environments = []
        self.spawner = _Spawner(ready=ready, pending=pending, events=self.events)
        self.user = _User(self.spawner, selected_items=selected_items)
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
        self.user.auth_state.update(tool_access_token="fresh-token")  # noqa: S106
        return self.user

    async def _authenticate_and_redirect(self, code):
        await DatalabLoginHandler._authenticate_and_redirect(self, code)

    async def _ensure_fresh_server(self, user, *, required):
        await DatalabLoginHandler._ensure_fresh_server(self, user, required=required)

    async def _await_server_future(self, future, failure_message):
        await DatalabLoginHandler._await_server_future(future, failure_message)

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
