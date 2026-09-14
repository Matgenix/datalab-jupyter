"""JupyterHub configuration for the Compose-managed datalab Tools deployment."""

import hashlib
import os
import sys

from traitlets.config import get_config

from datalab_jupyter.hub import DatalabAuthenticator
from datalab_jupyter.urls import configured_base_url

c = get_config()


def required_environment(name: str) -> str:
    """Return a required, non-empty deployment setting."""

    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be configured for the managed JupyterHub")
    return value


base_url = configured_base_url()
network_name = required_environment("DATALAB_JUPYTER_DOCKER_NETWORK")
volume_prefix = required_environment("DATALAB_JUPYTER_VOLUME_PREFIX")
client_secret = required_environment("DATALAB_JUPYTER_CLIENT_SECRET")
if not os.environ.get("JUPYTERHUB_CRYPT_KEY", "").strip():
    key_material = f"datalab-jupyter-auth-state:{client_secret}".encode()
    os.environ["JUPYTERHUB_CRYPT_KEY"] = hashlib.sha256(key_material).hexdigest()

c.JupyterHub.authenticator_class = DatalabAuthenticator
c.DatalabAuthenticator.api_url = required_environment("DATALAB_JUPYTER_API_URL")
c.DatalabAuthenticator.client_id = required_environment("DATALAB_JUPYTER_CLIENT_ID")
c.DatalabAuthenticator.client_secret = client_secret
c.DatalabAuthenticator.allow_all = True
c.DatalabAuthenticator.auto_login = True
c.DatalabAuthenticator.enable_auth_state = True

c.JupyterHub.bind_url = f"http://0.0.0.0:8000{base_url}"
c.JupyterHub.hub_ip = "0.0.0.0"
c.JupyterHub.hub_connect_ip = os.environ.get("DATALAB_JUPYTER_CONNECT_IP", "datalab-jupyter")
c.JupyterHub.cookie_secret_file = "/srv/jupyterhub/jupyterhub_cookie_secret"
c.JupyterHub.db_url = "sqlite:////srv/jupyterhub/jupyterhub.sqlite"

c.JupyterHub.spawner_class = "dockerspawner.DockerSpawner"
c.DockerSpawner.image = required_environment("DATALAB_JUPYTER_SINGLEUSER_IMAGE")
c.DockerSpawner.prefix = volume_prefix
c.DockerSpawner.network_name = network_name
c.DockerSpawner.use_internal_ip = True
c.DockerSpawner.remove = True
c.DockerSpawner.cmd = ["datalab-jupyter-singleuser"]
c.DockerSpawner.notebook_dir = "/home/jovyan/work"
c.DockerSpawner.environment = {
    "HOME": "/home/jovyan",
    "IPYTHONDIR": "/home/jovyan/.ipython",
    "USER": "jovyan",
}
c.DockerSpawner.volumes = {
    f"{volume_prefix}-{{username}}": "/home/jovyan/work",
}
c.DockerSpawner.extra_create_kwargs = {
    "user": "1000:100",
    "working_dir": "/home/jovyan/work",
}

c.Spawner.default_url = "/lab"
c.Spawner.cpu_limit = float(os.environ.get("DATALAB_JUPYTER_CPU_LIMIT", "2"))
c.Spawner.mem_limit = os.environ.get("DATALAB_JUPYTER_MEM_LIMIT", "4G")
c.Spawner.start_timeout = int(os.environ.get("DATALAB_JUPYTER_START_TIMEOUT", "120"))

idle_timeout = os.environ.get("DATALAB_JUPYTER_IDLE_TIMEOUT", "3600")
maximum_age = os.environ.get("DATALAB_JUPYTER_MAX_AGE", "86400")
c.JupyterHub.services = [
    {
        "name": "jupyterhub-idle-culler-service",
        "command": [
            sys.executable,
            "-m",
            "jupyterhub_idle_culler",
            f"--timeout={idle_timeout}",
            f"--max-age={maximum_age}",
        ],
    }
]
c.JupyterHub.load_roles = [
    {
        "name": "jupyterhub-idle-culler-role",
        "scopes": [
            "list:users",
            "read:users:activity",
            "read:servers",
            "delete:servers",
        ],
        "services": ["jupyterhub-idle-culler-service"],
    }
]
