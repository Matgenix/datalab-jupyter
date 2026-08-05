"""Add the datalab introduction to newly created notebooks."""

from pathlib import Path
from typing import Any

import nbformat

_BANNER_PATH = Path.home() / ".local" / "share" / "datalab-jupyter" / "banner.html"


def _is_blank_notebook(notebook: Any) -> bool:
    """Return whether a new notebook contains no user content."""

    return all(
        not str(cell.get("source", "")).strip()
        and not cell.get("outputs")
        and not cell.get("attachments")
        for cell in notebook.get("cells", [])
    )


def add_datalab_banner(model: dict[str, Any], path: str, contents_manager: Any) -> None:
    """Add an explanatory Markdown cell to a genuinely new, blank notebook."""

    if model.get("type") != "notebook" or contents_manager.exists(path):
        return

    notebook = model.get("content")
    if not notebook or not _is_blank_notebook(notebook):
        return

    try:
        banner_html = _BANNER_PATH.read_text(encoding="utf-8")
    except OSError:
        contents_manager.log.warning(
            "Unable to read the datalab notebook banner at %s", _BANNER_PATH
        )
        return

    cells = notebook.setdefault("cells", [])
    if not cells:
        cells.append(nbformat.v4.new_code_cell())

    banner = nbformat.v4.new_markdown_cell(
        banner_html,
        metadata={
            "deletable": True,
            "editable": False,
        },
    )
    cells.insert(0, banner)


def _load_jupyter_server_extension(server_app: Any) -> None:
    """Register the banner hook without replacing other notebook save hooks."""

    from .selected_items import register_selected_items_handler

    server_app.contents_manager.register_pre_save_hook(add_datalab_banner)
    register_selected_items_handler(server_app)
