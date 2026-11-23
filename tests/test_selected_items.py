import asyncio
import copy
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, patch

from tornado import web

from datalab_jupyter.server import selected_items as selected_items_module
from datalab_jupyter.server.selected_items import (
    SelectedItemsHandler,
    _create_selected_items_notebook,
    _initialization_source,
    _selection_banner,
    _unused_notebook_path,
)


class SelectedItemNotebookTests(TestCase):
    def test_content_is_visible_and_rerunnable(self):
        source = _initialization_source(("lab:one", "lab:two"))
        banner = _selection_banner(2)

        class Datalab:
            calls = []

            @classmethod
            def get_item(cls, *, refcode, load_blocks):
                cls.calls.append((refcode, load_blocks))
                if refcode == "lab:two":
                    raise ValueError("not readable")
                return {"refcode": refcode}

        namespace = {"datalab": Datalab}
        exec(source, namespace)  # noqa: S102
        exec(source, namespace)  # noqa: S102

        self.assertEqual(namespace["selected_item_refcodes"], ["lab:one", "lab:two"])
        self.assertEqual(namespace["selected_items"], [{"refcode": "lab:one"}])
        self.assertEqual(namespace["selected_item_errors"], {"lab:two": "not readable"})
        self.assertEqual(
            Datalab.calls,
            [
                ("lab:one", False),
                ("lab:two", False),
                ("lab:one", False),
                ("lab:two", False),
            ],
        )
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
        self.log = SimpleNamespace(
            exception=lambda *_args: None,
            warning=lambda *_args: None,
        )

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
            patch(
                "datalab_jupyter.urls.new_workspace_id",
                return_value="workspace-id",
            ),
        ):
            await SelectedItemsHandler.post(handler)

        exchange_mock.assert_awaited_once_with("notebook-code")
        create_mock.assert_awaited_once_with(handler, ("lab:one",))
        self.assertEqual(
            handler.redirected_to,
            (
                "/user/test/lab/workspaces/workspace-id/tree/Selected%20items.ipynb",
                False,
            ),
        )
        self.assertEqual(handler.headers["Cache-Control"], "no-store")
        self.assertNotIn("notebook-code", handler.request.uri)

    async def test_post_does_not_fall_back_to_a_query_code(self):
        handler = _Handler(code=None)
        handler.request.uri += "?notebook_launch_code=query-secret"

        with self.assertRaises(web.HTTPError) as raised:
            await SelectedItemsHandler.post(handler)

        self.assertEqual(raised.exception.status_code, 403)

    async def test_post_requires_authentication(self):
        handler = _Handler(authenticated=False)

        with self.assertRaises(web.HTTPError) as raised:
            SelectedItemsHandler.post(handler)

        self.assertEqual(raised.exception.status_code, 403)

    async def test_consumed_code_cannot_be_replayed_after_notebook_failure(self):
        first = _Handler()
        replay = _Handler()
        exchange = AsyncMock(
            side_effect=[
                ("lab:one",),
                web.HTTPError(403, "Invalid or expired notebook launch code"),
            ]
        )

        with (
            patch.object(selected_items_module, "_exchange_selection", new=exchange),
            patch.object(
                selected_items_module,
                "_create_selected_items_notebook",
                new=AsyncMock(side_effect=RuntimeError("save failed")),
            ),
        ):
            with self.assertRaises(web.HTTPError) as first_error:
                await SelectedItemsHandler.post(first)
            with self.assertRaises(web.HTTPError) as replay_error:
                await SelectedItemsHandler.post(replay)

        self.assertEqual(first_error.exception.status_code, 500)
        self.assertEqual(replay_error.exception.status_code, 403)
        self.assertEqual(exchange.await_count, 2)

    async def test_simultaneous_codes_open_their_own_items_in_distinct_workspaces(self):
        first = _Handler(code="first-code")
        second = _Handler(code="second-code")

        async def exchange(code):
            return (f"lab:{code}",)

        async def create(_handler, item_refcodes):
            return f"{item_refcodes[0]}.ipynb"

        with (
            patch.object(selected_items_module, "_exchange_selection", new=exchange),
            patch.object(selected_items_module, "_create_selected_items_notebook", new=create),
            patch(
                "datalab_jupyter.urls.new_workspace_id",
                side_effect=["workspace-one", "workspace-two"],
            ),
        ):
            await asyncio.gather(
                SelectedItemsHandler.post(first),
                SelectedItemsHandler.post(second),
            )

        self.assertNotEqual(first.redirected_to[0], second.redirected_to[0])
        self.assertIn("workspace-one", first.redirected_to[0])
        self.assertIn("lab%3Afirst-code.ipynb", first.redirected_to[0])
        self.assertIn("workspace-two", second.redirected_to[0])
        self.assertIn("lab%3Asecond-code.ipynb", second.redirected_to[0])


class _ContentsManager:
    def __init__(self, *, existing=(), fail_save_call=None, failure=None, cleanup_error=None):
        self.files = set(existing)
        self.exists_calls = []
        self.saved_models = {}
        self.deleted = []
        self.save_calls = 0
        self.fail_save_call = fail_save_call
        self.failure = failure or RuntimeError("save failed")
        self.cleanup_error = cleanup_error

    async def exists(self, path):
        self.exists_calls.append(path)
        await asyncio.sleep(0)
        return path in self.files

    async def save(self, model, path):
        self.save_calls += 1
        if self.save_calls == self.fail_save_call:
            raise self.failure
        self.files.add(path)
        self.saved_models.setdefault(path, []).append(copy.deepcopy(model))

    async def delete_file(self, path):
        self.deleted.append(path)
        if self.cleanup_error is not None:
            raise self.cleanup_error
        self.files.discard(path)


class _KernelManager:
    def __init__(self, *, start_error=None, cleanup_error=None):
        self.started = []
        self.stopped = []
        self.start_error = start_error
        self.cleanup_error = cleanup_error

    async def start_kernel(self, *, path, kernel_name):
        if self.start_error is not None:
            raise self.start_error
        kernel_id = f"kernel-{len(self.started) + 1}"
        self.started.append((kernel_id, path, kernel_name))
        return kernel_id

    async def shutdown_kernel(self, kernel_id, *, now):
        self.stopped.append((kernel_id, now))
        if self.cleanup_error is not None:
            raise self.cleanup_error


class _SessionManager:
    def __init__(self, *, kernel_manager=None, create_error=None, cleanup_error=None):
        self.created = []
        self.deleted = []
        self.kernel_manager = kernel_manager
        self.create_error = create_error
        self.cleanup_error = cleanup_error

    async def create_session(self, **kwargs):
        if self.create_error is not None:
            raise self.create_error
        session_id = f"session-{len(self.created) + 1}"
        self.created.append({"id": session_id, **kwargs})
        return {"id": session_id}

    async def delete_session(self, session_id):
        self.deleted.append(session_id)
        if self.cleanup_error is not None:
            raise self.cleanup_error
        if self.kernel_manager is not None:
            session = next(session for session in self.created if session["id"] == session_id)
            await self.kernel_manager.shutdown_kernel(session["kernel_id"], now=True)


class _NotebookHandler:
    def __init__(self, contents=None, kernels=None, sessions=None):
        self.contents_manager = contents or _ContentsManager()
        self.kernel_manager = kernels or _KernelManager()
        self.session_manager = sessions or _SessionManager(kernel_manager=self.kernel_manager)
        if self.session_manager.kernel_manager is None:
            self.session_manager.kernel_manager = self.kernel_manager
        self.warnings = []
        self.log = SimpleNamespace(warning=lambda *args: self.warnings.append(args))


class SelectedItemResourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_random_name_collision_retries_without_overwriting(self):
        stem = "Selected datalab items 2026-11-23 120000"
        contents = _ContentsManager(existing={f"{stem} collision.ipynb"})

        with (
            patch.object(
                selected_items_module,
                "datetime",
                wraps=selected_items_module.datetime,
            ) as datetime_mock,
            patch.object(
                selected_items_module.secrets,
                "token_urlsafe",
                side_effect=["collision", "fresh"],
            ),
        ):
            datetime_mock.now.return_value = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
            path = await _unused_notebook_path(contents)

        self.assertEqual(path, f"{stem} fresh.ipynb")
        self.assertEqual(
            contents.exists_calls,
            [f"{stem} collision.ipynb", f"{stem} fresh.ipynb"],
        )
        self.assertIn(f"{stem} collision.ipynb", contents.files)

    async def test_parallel_selections_get_distinct_notebooks_kernels_and_sessions(self):
        handler = _NotebookHandler()

        async def execute(_kernel_manager, kernel_id, source):
            self.assertIn(kernel_id, {"kernel-1", "kernel-2"})
            self.assertIn("selected_item_refcodes", source)
            await asyncio.sleep(0)
            return 1

        with (
            patch.object(
                selected_items_module,
                "_BANNER_PATH",
                SimpleNamespace(read_text=lambda **_kwargs: "banner"),
            ),
            patch.object(selected_items_module, "_execute_initialization", new=execute),
        ):
            paths = await asyncio.gather(
                _create_selected_items_notebook(handler, ("lab:one",)),
                _create_selected_items_notebook(handler, ("lab:two",)),
            )

        self.assertEqual(len(set(paths)), 2)
        self.assertEqual(len(handler.kernel_manager.started), 2)
        self.assertEqual(len(handler.session_manager.created), 2)
        self.assertEqual(
            {session["path"] for session in handler.session_manager.created},
            set(paths),
        )
        self.assertEqual(
            {session["kernel_id"] for session in handler.session_manager.created},
            {"kernel-1", "kernel-2"},
        )
        final_sources = {
            models[-1]["content"]["cells"][2]["source"]
            for models in handler.contents_manager.saved_models.values()
        }
        self.assertTrue(any("lab:one" in source for source in final_sources))
        self.assertTrue(any("lab:two" in source for source in final_sources))

    async def test_failures_clean_up_only_resources_created_by_the_request(self):
        cases = (
            ("initial save", {"fail_save_call": 1}, None, None, (0, 0, 0)),
            ("kernel start", {}, RuntimeError("kernel failed"), None, (1, 0, 0)),
            ("session create", {}, None, RuntimeError("session failed"), (1, 1, 0)),
            ("final save", {"fail_save_call": 2}, None, None, (1, 1, 1)),
        )
        for name, contents_options, kernel_error, session_error, cleanup_counts in cases:
            with self.subTest(name=name):
                contents = _ContentsManager(existing={"pre-existing.ipynb"}, **contents_options)
                kernels = _KernelManager(start_error=kernel_error)
                sessions = _SessionManager(create_error=session_error)
                handler = _NotebookHandler(contents, kernels, sessions)

                with (
                    patch.object(
                        selected_items_module,
                        "_BANNER_PATH",
                        SimpleNamespace(read_text=lambda **_kwargs: "banner"),
                    ),
                    patch.object(
                        selected_items_module,
                        "_execute_initialization",
                        new=AsyncMock(return_value=1),
                    ),
                ):
                    with self.assertRaises(RuntimeError):
                        await _create_selected_items_notebook(handler, ("lab:one",))

                file_deletes, kernel_stops, session_deletes = cleanup_counts
                self.assertEqual(len(contents.deleted), file_deletes)
                self.assertEqual(len(kernels.stopped), kernel_stops)
                self.assertEqual(len(sessions.deleted), session_deletes)
                self.assertIn("pre-existing.ipynb", contents.files)
                self.assertNotIn("pre-existing.ipynb", contents.deleted)

    async def test_initialization_failure_keeps_rerunnable_notebook_and_session(self):
        handler = _NotebookHandler()

        with (
            patch.object(
                selected_items_module,
                "_BANNER_PATH",
                SimpleNamespace(read_text=lambda **_kwargs: "banner"),
            ),
            patch.object(
                selected_items_module,
                "_execute_initialization",
                new=AsyncMock(side_effect=RuntimeError("execution failed")),
            ),
        ):
            path = await _create_selected_items_notebook(handler, ("lab:one",))

        initialization = handler.contents_manager.saved_models[path][-1]["content"]["cells"][2]
        self.assertIn("selected_item_refcodes", initialization["source"])
        self.assertIn("Run this cell to try again", initialization["outputs"][0]["text"])
        self.assertEqual(handler.contents_manager.deleted, [])
        self.assertEqual(handler.kernel_manager.stopped, [])
        self.assertEqual(handler.session_manager.deleted, [])

    async def test_cleanup_failures_preserve_the_primary_exception(self):
        primary = RuntimeError("final save failed")
        cleanup = RuntimeError("cleanup failed")
        handler = _NotebookHandler(
            _ContentsManager(fail_save_call=2, failure=primary, cleanup_error=cleanup),
            _KernelManager(cleanup_error=cleanup),
            _SessionManager(cleanup_error=cleanup),
        )

        with (
            patch.object(
                selected_items_module,
                "_BANNER_PATH",
                SimpleNamespace(read_text=lambda **_kwargs: "banner"),
            ),
            patch.object(
                selected_items_module,
                "_execute_initialization",
                new=AsyncMock(return_value=1),
            ),
        ):
            with self.assertRaises(RuntimeError) as raised:
                await _create_selected_items_notebook(handler, ("lab:one",))

        self.assertIs(raised.exception, primary)
        self.assertEqual(len(handler.warnings), 3)
