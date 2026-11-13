import unittest
from types import SimpleNamespace

from datalab_jupyter.hub.authenticator import DatalabLoginHandler


class _SpawnCompletion:
    def __init__(self, spawner):
        self.spawner = spawner
        self.awaited = False

    def __await__(self):
        self.awaited = True
        self.spawner.ready = True
        if False:
            yield None
        return None


class _User:
    url = "/user/test/"

    def __init__(self, spawner):
        self.spawners = {"": spawner}

    async def get_auth_state(self):
        return {"notebook_launch_code": "selected-items-code"}


class _Handler:
    def __init__(self):
        self.request = SimpleNamespace(uri="/hub/datalab-login?datalab_launch_code=secret")
        self.statsd = SimpleNamespace(incr=lambda _name: None)
        self.headers = {}
        self.redirected_to = None
        self.spawner = SimpleNamespace(ready=False, pending=True, _spawn_future=None)
        self.spawner._spawn_future = _SpawnCompletion(self.spawner)
        self.user = _User(self.spawner)

    def get_argument(self, name, default):
        return "secret" if name == "datalab_launch_code" else default

    def set_header(self, name, value):
        self.headers[name] = value

    async def login_user(self):
        return self.user

    def get_next_url(self, _user):
        return "/hub/home"

    async def spawn_single_user(self, _user):
        raise AssertionError("a pending spawn must not be started twice")

    def redirect(self, url, permanent):
        self.redirected_to = (url, permanent)


class LoginHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_spawn_future_is_awaited_before_selected_items_redirect(self):
        handler = _Handler()

        await DatalabLoginHandler.get(handler)

        self.assertTrue(handler.spawner._spawn_future.awaited)
        self.assertEqual(
            handler.redirected_to,
            (
                "/user/test/datalab/open-selected?notebook_launch_code=selected-items-code",
                False,
            ),
        )
        self.assertNotIn("secret", handler.request.uri)
        self.assertEqual(handler.headers["Cache-Control"], "no-store")
