"""Install the IPython preload and bootstrap a datalab single-user server."""

import os
import sys
from importlib import resources
from pathlib import Path

import nbformat


def bootstrap() -> None:
    """Install managed startup assets and create the welcome notebook once."""

    home = Path.home()

    startup_directory = home / ".ipython" / "profile_default" / "startup"
    startup_directory.mkdir(parents=True, exist_ok=True)
    startup_source = resources.files("datalab_jupyter.resources").joinpath("ipython_startup.py")
    (startup_directory / "10-datalab.py").write_text(
        startup_source.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    data_directory = home / ".local" / "share" / "datalab-jupyter"
    data_directory.mkdir(parents=True, exist_ok=True)
    banner_source = resources.files("datalab_jupyter.resources").joinpath("banner.html")
    (data_directory / "banner.html").write_text(
        banner_source.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    target = home / "work" / "Welcome to datalab.ipynb"
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    notebook = nbformat.v4.new_notebook(
        cells=[
            nbformat.v4.new_markdown_cell(
                "# Welcome to datalab\n\n"
                "This JupyterLab session uses your current datalab account and permissions. "
                "Its tool access token expires automatically and is not your "
                "permanent API key.\n\n"
                "New notebooks include a short reminder about the two objects "
                "preloaded in every Python kernel."
            ),
            nbformat.v4.new_code_cell(
                "# These objects are preloaded in every kernel.\ncurrent_user\ndatalab"
            ),
            nbformat.v4.new_code_cell(
                "# Fetch items visible to your datalab account.\nitems = datalab.get_items()\nitems"
            ),
        ],
    )
    nbformat.write(notebook, target)


def singleuser_main() -> None:
    """Bootstrap and replace this process with JupyterHub's single-user server."""

    bootstrap()
    os.execvp("jupyterhub-singleuser", ["jupyterhub-singleuser", *sys.argv[1:]])
