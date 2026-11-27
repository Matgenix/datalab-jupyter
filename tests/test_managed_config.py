import runpy
from pathlib import Path

import pytest

CONFIG_PATH = Path(__file__).parents[1] / "jupyterhub_config.py"
REQUIRED_ENVIRONMENT = {
    "DATALAB_JUPYTER_API_URL": "http://datalab-api:5001",
    "DATALAB_JUPYTER_BROWSER_API_URL": "https://api.example.test",
    "DATALAB_JUPYTER_CLIENT_ID": "datalab-jupyter",
    "DATALAB_JUPYTER_CLIENT_SECRET": "a-secure-test-client-secret",
    "DATALAB_JUPYTER_DOCKER_NETWORK": "datalab_tools",
    "DATALAB_JUPYTER_SINGLEUSER_IMAGE": "datalab-jupyter:test",
    "DATALAB_JUPYTER_VOLUME_PREFIX": "datalab-jupyter",
}


def load_managed_config(monkeypatch, pids_limit=None):
    for name, value in REQUIRED_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    if pids_limit is None:
        monkeypatch.delenv("DATALAB_JUPYTER_PIDS_LIMIT", raising=False)
    else:
        monkeypatch.setenv("DATALAB_JUPYTER_PIDS_LIMIT", pids_limit)

    return runpy.run_path(str(CONFIG_PATH))["c"]


def test_managed_config_applies_default_container_controls(monkeypatch):
    config = load_managed_config(monkeypatch)

    assert config.DockerSpawner.extra_host_config == {
        "pids_limit": 512,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
    }


def test_managed_config_applies_configured_pid_limit(monkeypatch):
    config = load_managed_config(monkeypatch, "1024")

    assert config.DockerSpawner.extra_host_config["pids_limit"] == 1024


@pytest.mark.parametrize("pids_limit", ("", "0", "-1", "1.5", "1_024", "not-a-number", "65537"))
def test_managed_config_rejects_invalid_pid_limit(monkeypatch, pids_limit):
    with pytest.raises(RuntimeError, match="DATALAB_JUPYTER_PIDS_LIMIT"):
        load_managed_config(monkeypatch, pids_limit)


def test_managed_config_retains_existing_resource_and_isolation_settings(monkeypatch):
    config = load_managed_config(monkeypatch)

    assert config.Spawner.cpu_limit == 2.0
    assert config.Spawner.mem_limit == "4G"
    assert config.DockerSpawner.extra_create_kwargs == {
        "user": "1000:100",
        "working_dir": "/home/jovyan/work",
    }
    assert config.DockerSpawner.volumes == {"datalab-jupyter-{username}": "/home/jovyan/work"}
    assert config.DockerSpawner.network_name == "datalab_tools"
    assert config.DockerSpawner.use_internal_ip is True
    assert config.DockerSpawner.remove is True
    assert config.JupyterHub.services[0]["command"][-2:] == [
        "--timeout=3600",
        "--max-age=86400",
    ]


def test_managed_config_wires_trusted_browser_and_hub_urls(monkeypatch):
    monkeypatch.setenv("DATALAB_JUPYTER_PUBLIC_URL", "https://hub.example.test/prefix/jupyter")
    config = load_managed_config(monkeypatch)

    assert config.DatalabAuthenticator.browser_api_url == "https://api.example.test"
    assert config.JupyterHub.public_url == "https://hub.example.test/prefix/jupyter/"
    assert config.JupyterHub.bind_url == "http://0.0.0.0:8000/prefix/jupyter/"


def test_managed_config_requires_browser_api_url_and_rejects_unsafe_public_urls(monkeypatch):
    for name, value in REQUIRED_ENVIRONMENT.items():
        if name != "DATALAB_JUPYTER_BROWSER_API_URL":
            monkeypatch.setenv(name, value)
    monkeypatch.delenv("DATALAB_JUPYTER_BROWSER_API_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATALAB_JUPYTER_BROWSER_API_URL"):
        runpy.run_path(str(CONFIG_PATH))

    monkeypatch.setenv("DATALAB_JUPYTER_BROWSER_API_URL", "https://api.example.test")
    monkeypatch.setenv("DATALAB_JUPYTER_PUBLIC_URL", "http://hub.example.test/jupyter")
    with pytest.raises(ValueError, match="must use HTTPS"):
        runpy.run_path(str(CONFIG_PATH))


def test_managed_config_rejects_unsafe_browser_api_url_by_default(monkeypatch):
    monkeypatch.setitem(
        REQUIRED_ENVIRONMENT,
        "DATALAB_JUPYTER_BROWSER_API_URL",
        "http://api.example.test",
    )
    with pytest.raises(ValueError, match="must use HTTPS"):
        load_managed_config(monkeypatch)


def test_managed_config_allows_http_preview_urls_with_explicit_exception(monkeypatch):
    monkeypatch.setenv("PYDATALAB_TOOLS__ALLOW_INSECURE_HTTP", "true")
    monkeypatch.setenv("DATALAB_JUPYTER_PUBLIC_URL", "http://hub.example.test/jupyter")
    monkeypatch.setitem(
        REQUIRED_ENVIRONMENT,
        "DATALAB_JUPYTER_BROWSER_API_URL",
        "http://api.example.test/api",
    )

    config = load_managed_config(monkeypatch)

    assert config.JupyterHub.public_url == "http://hub.example.test/jupyter/"
    assert config.DatalabAuthenticator.browser_api_url == "http://api.example.test/api"


def test_managed_config_allows_http_app_url_fallback_with_explicit_exception(monkeypatch):
    monkeypatch.setenv("PYDATALAB_TOOLS__ALLOW_INSECURE_HTTP", "true")
    monkeypatch.setenv("PYDATALAB_APP_URL", "http://datalab.example.test")

    config = load_managed_config(monkeypatch)

    assert config.JupyterHub.public_url == "http://datalab.example.test/jupyter/"


def test_managed_config_rejects_invalid_insecure_http_setting(monkeypatch):
    monkeypatch.setenv("PYDATALAB_TOOLS__ALLOW_INSECURE_HTTP", "sometimes")

    with pytest.raises(ValueError, match="must be a boolean"):
        load_managed_config(monkeypatch)
