# datalab-jupyter

`datalab-jupyter` connects the datalab Tools framework to JupyterHub and to the
Jupyter user environments started by that Hub. It is one Python distribution
with independently installable components; it is not a JupyterLab frontend
extension.

## Components

- `datalab-jupyter[plugin]` installs the concrete `jupyter` tool provider in a
  datalab API environment.
- `datalab-jupyter[hub]` installs the datalab JupyterHub authenticator.
- `datalab-jupyter[server]` installs the Jupyter Server and IPython integration
  in each single-user environment.

The host datalab installation supplies the provider's core API; the
`plugin-dev` dependency group supplies it for isolated development. Since both
datalab and JupyterHub use Pydantic 2, that development group can be installed
alongside the Hub and server extras when one environment needs all components.

The tool provider asks datalab core for a one-use launch grant. The Hub exchanges
that grant with its client credentials, stores the resulting temporary tool
token in encrypted JupyterHub `auth_state`, and injects it only into the matching
user server. The user server preloads an authenticated `DatalabClient` and can
create a notebook from items selected in datalab.

## Install the datalab tool plugin

For a checkout beside the datalab repository, add the following to datalab's
ignored root `plugins.toml`:

```toml
dependencies = ["datalab-jupyter[plugin]"]

[tool.uv.sources]
datalab-jupyter = { path = "dev-repos/datalab-jupyter", editable = true }
```

Then run `uv run invoke dev.install` from `pydatalab/`. A production deployment
should replace the path source with a versioned Git or package source.

The provider requires:

```shell
export DATALAB_JUPYTER_CLIENT_ID=datalab-jupyter
export DATALAB_JUPYTER_CLIENT_SECRET='<at least 32 characters>'
```

Set `DATALAB_JUPYTER_EXTERNAL_URL` for an independently deployed Hub, or
`DATALAB_JUPYTER_PUBLIC_URL` to override the browser-facing URL of the managed
Hub. If neither is set, the provider derives `/jupyter/` from the datalab
application URL. Disable the installed tool with
`PYDATALAB_TOOLS__DISABLED='["jupyter"]'`.

Public and external URLs must use HTTPS unless they target a loopback address. For a trusted,
temporary preview reached by plain HTTP, set `DATALAB_JUPYTER_ALLOW_INSECURE_HTTP=true` in the
root `.env`. The Jupyter Compose file passes it to the Datalab API, where the plugin validates its
public URL. This safety override is disabled by default and must not be used in production.

## Configure an external JupyterHub

Install `[hub]` in the Hub environment and `[server]` in the single-user image.
Configure the Hub with:

```python
from datalab_jupyter.hub import DatalabAuthenticator

c.JupyterHub.authenticator_class = DatalabAuthenticator
c.DatalabAuthenticator.api_url = "https://api.example.org"
c.DatalabAuthenticator.client_id = "datalab-jupyter"
c.DatalabAuthenticator.client_secret = "<the same deployment secret>"
c.DatalabAuthenticator.enable_auth_state = True
c.DatalabAuthenticator.allow_all = True
c.DatalabAuthenticator.auto_login = True
```

Set a persistent `JUPYTERHUB_CRYPT_KEY`, and use
`datalab-jupyter-singleuser` as the spawner command. The deployment owns its
spawner, storage, TLS, resource limits, culling, and user image. User-server
lifetimes must not exceed the delegated datalab session lifetime.

## Managed image

The included Dockerfile builds the combined Hub/single-user image used by
datalab's optional Jupyter companion Compose file:

```shell
docker build -t datalab-jupyter:0.1.0 .
```

The image expects `DATALAB_JUPYTER_API_URL`, `DATALAB_JUPYTER_CLIENT_ID`,
`DATALAB_JUPYTER_CLIENT_SECRET`, `DATALAB_JUPYTER_DOCKER_NETWORK`,
`DATALAB_JUPYTER_VOLUME_PREFIX`, and `DATALAB_JUPYTER_SINGLEUSER_IMAGE`.
Optional settings include `DATALAB_JUPYTER_PUBLIC_URL`,
`DATALAB_JUPYTER_ALLOW_INSECURE_HTTP`,
`DATALAB_JUPYTER_CONNECT_IP`, `DATALAB_JUPYTER_CPU_LIMIT`,
`DATALAB_JUPYTER_MEM_LIMIT`, `DATALAB_JUPYTER_START_TIMEOUT`,
`DATALAB_JUPYTER_IDLE_TIMEOUT`, and `DATALAB_JUPYTER_MAX_AGE`.

The volume prefix is also used as DockerSpawner's container prefix. This keeps spawned notebook
containers and their persistent work volumes within one deployment-specific namespace.

## Development

```shell
uv sync --extra plugin --group plugin-dev --dev
uv sync --extra hub --extra server --dev
uv run pre-commit run --all-files
uv build
```
