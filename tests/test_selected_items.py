import unittest
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, patch

from tornado import web

from datalab_jupyter.server import selected_items as selected_items_module
from datalab_jupyter.server.selected_items import (
    SelectedItemsHandler,
    _initialization_source,
    _selection_banner,
)


class SelectedItemNotebookTests(TestCase):
    def test_content_is_visible_and_rerunnable(self):
        source = _initialization_source(("lab:one", "lab:two"))
        banner = _selection_banner(2)

        self.assertIn("selected_item_refcodes", source)
        self.assertIn('"lab:one"', source)
        self.assertIn("selected_item_errors", source)
        self.assertIn("2 items", banner)


class _Handler:
    xsrf_token = b"xsrf-token"

    def __init__(self, code="notebook-code", authenticated=True):
        self.current_user = object() if authenticated else None
        self.request = SimpleNamespace(
            method="POST",
            path="/user/test/datalab/open-selected",
            uri="/user/test/datalab/open-selected",
        )
        self.base_url = "/user/test/"
        self.code = code
        self.headers = {}
        self.finished = None
        self.redirected_to = None
        self.log = SimpleNamespace(exception=lambda *_args: None)

    def get_body_argument(self, name, default):
        return self.code if name == "notebook_launch_code" else default

    def set_header(self, name, value):
        self.headers[name] = value

    def finish(self, value):
        self.finished = value

    def redirect(self, url, permanent):
        self.redirected_to = (url, permanent)


class SelectedItemsHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_renders_bootstrap_without_exchanging_the_code(self):
        handler = _Handler()
        handler.request.method = "GET"

        with patch.object(
            selected_items_module,
            "_exchange_selection",
            new_callable=AsyncMock,
        ) as exchange_mock:
            await SelectedItemsHandler.get(handler)

        exchange_mock.assert_not_awaited()
        self.assertIn('name="notebook_launch_code"', handler.finished)
        self.assertEqual(handler.headers["Cache-Control"], "no-store")

    async def test_post_exchanges_body_code_once_and_redirects_to_the_notebook(self):
        handler = _Handler()

        with (
            patch.object(
                selected_items_module,
                "_exchange_selection",
                new=AsyncMock(return_value=("lab:one",)),
            ) as exchange_mock,
            patch.object(
                selected_items_module,
                "_create_selected_items_notebook",
                new=AsyncMock(return_value="Selected items.ipynb"),
            ) as create_mock,
        ):
            await SelectedItemsHandler.post(handler)

        exchange_mock.assert_awaited_once_with("notebook-code")
        create_mock.assert_awaited_once_with(handler, ("lab:one",))
        self.assertEqual(
            handler.redirected_to,
            ("/user/test/lab/tree/Selected%20items.ipynb", False),
        )
        self.assertNotIn("notebook-code", handler.request.uri)

    async def test_post_does_not_fall_back_to_a_query_code(self):
        handler = _Handler(code=None)
        handler.request.uri += "?notebook_launch_code=query-secret"

        with self.assertRaises(web.HTTPError) as raised:
            await SelectedItemsHandler.post(handler)

        self.assertEqual(raised.exception.status_code, 403)

    async def test_post_preserves_invalid_or_expired_code_failures(self):
        handler = _Handler()

        with patch.object(
            selected_items_module,
            "_exchange_selection",
            new=AsyncMock(side_effect=web.HTTPError(403, "Invalid or expired code")),
        ):
            with self.assertRaises(web.HTTPError) as raised:
                await SelectedItemsHandler.post(handler)

        self.assertEqual(raised.exception.status_code, 403)

    async def test_post_requires_authentication(self):
        handler = _Handler(authenticated=False)

        with self.assertRaises(web.HTTPError) as raised:
            SelectedItemsHandler.post(handler)

        self.assertEqual(raised.exception.status_code, 403)
