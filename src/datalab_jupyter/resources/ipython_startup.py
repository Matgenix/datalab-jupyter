"""Preload the current datalab identity and API client in every IPython kernel."""


def _load_datalab_objects() -> tuple[object, dict[str, object]]:
    import json
    import os

    from datalab_api import DatalabClient
    from IPython import get_ipython

    api_url = os.environ.get("DATALAB_API_URL", "").strip()
    current_user_json = os.environ.get("DATALAB_CURRENT_USER_JSON", "").strip()
    api_key = os.environ.get("DATALAB_API_KEY", "").strip()
    if not api_url or not current_user_json or not api_key:
        raise RuntimeError(
            "The datalab kernel environment is incomplete. "
            "Stop this server and launch JupyterLab again from datalab."
        )

    try:
        user = json.loads(current_user_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError("The datalab current-user snapshot is invalid.") from exc
    if not isinstance(user, dict) or not user.get("id"):
        raise RuntimeError("The datalab current-user snapshot is incomplete.")

    client = DatalabClient(api_url)
    shell = get_ipython()
    if shell is not None:
        objects = {"datalab": client, "current_user": user}

        def reveal_preloaded_objects(*_args: object, **_kwargs: object) -> None:
            # IPython hides startup-file variables after this file runs. Reveal
            # only the two documented objects immediately before the first cell.
            shell.push(objects, interactive=True)
            shell.events.unregister("pre_run_cell", reveal_preloaded_objects)

        shell.events.register("pre_run_cell", reveal_preloaded_objects)
        shell.banner2 = (
            "Connected to datalab.\n"
            "Preloaded objects: datalab (authenticated client) and "
            "current_user (identity snapshot).\n"
        )

    return client, user


datalab, current_user = _load_datalab_objects()
del _load_datalab_objects
