"""Create a notebook from an item selection handed off by datalab."""

import asyncio
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import nbformat
from jupyter_core.utils import ensure_async
from jupyter_server.base.handlers import JupyterHandler
from jupyter_server.utils import url_path_join
from tornado import web

from datalab_jupyter.fragment_post import render_fragment_post
from datalab_jupyter.urls import workspace_url

_BANNER_PATH = Path.home() / ".local" / "share" / "datalab-jupyter" / "banner.html"
_EXCHANGE_PATH = "/v0.1/tools/jupyter/selection/exchange"
_ACTION_ID = "open-in-notebook"
_MAX_ITEMS = 20
_NOTEBOOK_ALLOCATION_ATTEMPTS = 2


async def _exchange_selection(code: str) -> tuple[str, ...]:
    """Exchange a one-time notebook launch code for immutable refcodes."""
    api_url = os.environ.get("DATALAB_API_URL", "").strip()
    tool_access_token = os.environ.get("DATALAB_API_KEY", "").strip()
    if not api_url or not tool_access_token:
        raise web.HTTPError(503, "The datalab notebook integration is not configured")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{api_url.rstrip('/')}/{_EXCHANGE_PATH.lstrip('/')}",
                json={"code": code},
                headers={"DATALAB-API-KEY": tool_access_token},
            )
    except httpx.RequestError as exc:
        raise web.HTTPError(
            503, "datalab is temporarily unavailable; select the items and try again"
        ) from exc

    if response.status_code in {400, 401, 403, 404, 409, 410}:
        raise web.HTTPError(403, "Invalid or expired notebook launch code")
    try:
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPStatusError, ValueError) as exc:
        raise web.HTTPError(502, "datalab returned an invalid selection response") from exc

    action_id = payload.get("action") if isinstance(payload, dict) else None
    item_refcodes = payload.get("items") if isinstance(payload, dict) else None
    if (
        action_id != _ACTION_ID
        or not isinstance(item_refcodes, list)
        or not 1 <= len(item_refcodes) <= _MAX_ITEMS
        or any(not isinstance(refcode, str) or not refcode for refcode in item_refcodes)
        or len(item_refcodes) != len(set(item_refcodes))
    ):
        raise web.HTTPError(502, "datalab returned an invalid item selection")
    return tuple(item_refcodes)


def _initialization_source(item_refcodes: tuple[str, ...]) -> str:
    """Return the visible, rerunnable selected-items initialization cell."""
    return (
        "# Rerun this cell after restarting or reopening the notebook kernel.\n"
        f"selected_item_refcodes = {json.dumps(item_refcodes, indent=4)}\n"
        "selected_items = []\n"
        "selected_item_errors = {}\n\n"
        "for refcode in selected_item_refcodes:\n"
        "    try:\n"
        "        selected_items.append(\n"
        "            datalab.get_item(refcode=refcode, load_blocks=False)\n"
        "        )\n"
        "    except Exception as error:\n"
        "        selected_item_errors[refcode] = str(error)\n"
    )


def _selection_banner(item_count: int) -> str:
    """Return the read-only explanation shown above the initialization cell."""
    return (
        '<div style="border-left: 4px solid #20b283; background: #f3faf7; '
        'padding: 12px 20px;">\n'
        "<strong>Started from selected datalab items</strong><br>\n"
        f"This notebook was created from <strong>{item_count} items</strong> selected "
        "in datalab. The initialization cell below loads them using your current "
        "datalab permissions. Successfully loaded items are available in "
        "<code>selected_items</code>; immutable refcodes are stored in "
        "<code>selected_item_refcodes</code>; failures are recorded in "
        "<code>selected_item_errors</code>.<br><br>\n"
        "The cell runs automatically only when this notebook is created. Rerun it "
        "after restarting the kernel or reopening the notebook with a fresh kernel.\n"
        "</div>"
    )


async def _unused_notebook_path(contents_manager: Any) -> str:
    """Return a readable notebook path that does not yet exist."""
    stem = datetime.now(timezone.utc).strftime("Selected datalab items %Y-%m-%d %H%M%S")
    for _attempt in range(_NOTEBOOK_ALLOCATION_ATTEMPTS):
        path = f"{stem} {secrets.token_urlsafe(16)}.ipynb"
        if not await ensure_async(contents_manager.exists(path)):
            return path
    raise RuntimeError("Unable to allocate a unique selected-items notebook name")


async def _cleanup_created_resources(
    handler: JupyterHandler,
    *,
    path: str,
    notebook_created: bool,
    kernel_id: str | None,
    session_id: str | None,
) -> None:
    """Best-effort cleanup of resources attributed to one failed request."""

    session_deleted = False
    if session_id is not None:
        try:
            await ensure_async(handler.session_manager.delete_session(session_id))
            # Jupyter Server owns the session's kernel and stops it when the
            # session is deleted, so only use direct shutdown as a fallback.
            session_deleted = True
        except Exception as exc:
            handler.log.warning("Unable to delete failed selected-items session: %s", exc)
    if kernel_id is not None and not session_deleted:
        try:
            await ensure_async(handler.kernel_manager.shutdown_kernel(kernel_id, now=True))
        except Exception as exc:
            handler.log.warning("Unable to stop failed selected-items kernel: %s", exc)
    if notebook_created:
        try:
            await ensure_async(handler.contents_manager.delete_file(path))
        except Exception as exc:
            handler.log.warning("Unable to delete failed selected-items notebook: %s", exc)


async def _execute_initialization(
    kernel_manager: Any,
    kernel_id: str,
    source: str,
) -> int:
    """Execute the initialization source once in the notebook's dedicated kernel."""
    client = kernel_manager.get_kernel(kernel_id).client()
    client.start_channels()
    try:
        await ensure_async(client.wait_for_ready(timeout=30))
        message_id = client.execute(source, store_history=True)
        reply = await ensure_async(client.get_shell_msg(timeout=30))
        while True:
            message = await ensure_async(client.get_iopub_msg(timeout=30))
            if (
                message.get("parent_header", {}).get("msg_id") == message_id
                and message.get("header", {}).get("msg_type") == "status"
                and message.get("content", {}).get("execution_state") == "idle"
            ):
                break
        content = reply.get("content", {})
        if content.get("status") != "ok":
            raise RuntimeError(content.get("evalue") or "The initialization cell failed")
        return int(content.get("execution_count") or 1)
    finally:
        client.stop_channels()


async def _create_selected_items_notebook(
    handler: JupyterHandler,
    item_refcodes: tuple[str, ...],
) -> str:
    """Create, start, initialize, and save one selected-items notebook."""
    try:
        banner_html = _BANNER_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Unable to read the datalab banner at {_BANNER_PATH}") from exc

    initialization = nbformat.v4.new_code_cell(_initialization_source(item_refcodes))
    read_only_metadata = {"deletable": True, "editable": False}
    notebook = nbformat.v4.new_notebook(
        cells=[
            nbformat.v4.new_markdown_cell(
                banner_html,
                metadata=read_only_metadata,
            ),
            nbformat.v4.new_markdown_cell(
                _selection_banner(len(item_refcodes)),
                metadata=read_only_metadata,
            ),
            initialization,
            nbformat.v4.new_code_cell(),
        ]
    )

    contents_manager = handler.contents_manager
    kernel_manager = handler.kernel_manager
    session_manager = handler.session_manager
    path = await _unused_notebook_path(contents_manager)
    model = {"type": "notebook", "format": "json", "content": notebook}
    notebook_created = False
    kernel_id: str | None = None
    session_id: str | None = None
    try:
        await ensure_async(contents_manager.save(model, path))
        notebook_created = True
        kernel_id = await ensure_async(
            kernel_manager.start_kernel(path=path, kernel_name="python3")
        )
        session = await ensure_async(
            session_manager.create_session(
                path=path,
                name=Path(path).name,
                type="notebook",
                kernel_id=kernel_id,
            )
        )
        session_id = session.get("id") if isinstance(session, dict) else None
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError("The selected-items Jupyter session has no identifier")

        try:
            initialization["execution_count"] = await _execute_initialization(
                kernel_manager,
                kernel_id,
                initialization["source"],
            )
        except Exception as exc:
            handler.log.warning("Unable to initialize selected datalab items: %s", exc)
            initialization["outputs"] = [
                nbformat.v4.new_output(
                    "stream",
                    name="stderr",
                    text=(
                        "The selected items could not be loaded automatically. "
                        "Run this cell to try again.\n"
                    ),
                )
            ]

        await ensure_async(contents_manager.save(model, path))
        return path
    except (Exception, asyncio.CancelledError):
        await _cleanup_created_resources(
            handler,
            path=path,
            notebook_created=notebook_created,
            kernel_id=kernel_id,
            session_id=session_id,
        )
        raise


class SelectedItemsHandler(JupyterHandler):
    """Receive one selected-items handoff and open its initialized notebook."""

    @web.authenticated
    async def get(self) -> None:
        """Render the authenticated fragment-to-POST bootstrap."""

        self.finish(render_fragment_post(self, "notebook_launch_code"))

    @web.authenticated
    async def post(self) -> None:
        """Exchange a body-only launch code and create the selected-items notebook."""

        code = (self.get_body_argument("notebook_launch_code", None) or "").strip()
        if not code:
            raise web.HTTPError(403, "Invalid or expired notebook launch code")

        self.set_header("Cache-Control", "no-store")
        self.set_header("Referrer-Policy", "no-referrer")

        item_refcodes = await _exchange_selection(code)
        try:
            path = await _create_selected_items_notebook(self, item_refcodes)
        except web.HTTPError:
            raise
        except Exception as exc:
            self.log.exception("Unable to create a selected-items notebook")
            raise web.HTTPError(500, "Unable to create the selected-items notebook") from exc

        notebook_url = workspace_url(self.base_url, notebook_path=path)
        self.redirect(notebook_url, permanent=False)


def register_selected_items_handler(server_app: Any) -> None:
    """Register the authenticated selected-items handler below the server base URL."""
    route = url_path_join(
        server_app.web_app.settings["base_url"],
        "datalab",
        "open-selected",
    )
    server_app.web_app.add_handlers(".*$", [(route, SelectedItemsHandler)])
