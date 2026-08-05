# datalab-jupyter

`datalab-jupyter` connects the Datalab Tools framework to JupyterHub and to the
Jupyter user environments started by that Hub. It is one Python distribution
with independently installable components; it is not a JupyterLab frontend
extension.

## Components

- `datalab-jupyter[plugin]` installs the concrete `jupyter` tool provider in a
  Datalab API environment.
- `datalab-jupyter[hub]` installs the Datalab JupyterHub authenticator.
- `datalab-jupyter[server]` installs the Jupyter Server and IPython integration
  in each single-user environment.

The plugin development environment is intentionally separate from `[hub]` and
`[server]`: Datalab core currently uses Pydantic 1 while JupyterHub 5 uses
Pydantic 2. The host Datalab installation supplies the provider's core API;
the `plugin-dev` dependency group supplies it only for isolated development.
The Hub and server extras can be installed together in one image.

The tool provider asks Datalab core for a one-use launch grant. The Hub exchanges
that grant with its client credentials, stores the resulting temporary tool
token in encrypted JupyterHub `auth_state`, and injects it only into the matching
user server. The user server preloads an authenticated `DatalabClient` and can
create a notebook from items selected in Datalab.

## Install the Datalab tool plugin

For a checkout beside the Datalab repository, add the following to Datalab's
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
Hub. If neither is set, the provider derives `/jupyter/` from the Datalab
application URL. Disable the installed tool with
`PYDATALAB_TOOLS__DISABLED='["jupyter"]'`.

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
lifetimes must not exceed the delegated Datalab session lifetime.

## Managed image

The included Dockerfile builds the combined Hub/single-user image used by the
Datalab Compose profile:

```shell
docker build -t datalab-jupyter:0.1.0 .
```

The image expects `DATALAB_JUPYTER_API_URL`, `DATALAB_JUPYTER_CLIENT_ID`,
`DATALAB_JUPYTER_CLIENT_SECRET`, `DATALAB_JUPYTER_DOCKER_NETWORK`,
`DATALAB_JUPYTER_VOLUME_PREFIX`, and `DATALAB_JUPYTER_SINGLEUSER_IMAGE`.
Optional settings include `DATALAB_JUPYTER_PUBLIC_URL`,
`DATALAB_JUPYTER_CONNECT_IP`, `DATALAB_JUPYTER_CPU_LIMIT`,
`DATALAB_JUPYTER_MEM_LIMIT`, `DATALAB_JUPYTER_START_TIMEOUT`,
`DATALAB_JUPYTER_IDLE_TIMEOUT`, and `DATALAB_JUPYTER_MAX_AGE`.

## Development

```shell
uv sync --extra plugin --group plugin-dev --dev
uv sync --extra hub --extra server --dev
uv run pre-commit run --all-files
uv build
```

The Datalab tool API is under active development. Until it is available from a
compatible Datalab release, development resolves `datalab-server` from the
matching Datalab development branch.
