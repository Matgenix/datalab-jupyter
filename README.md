# datalab-jupyter

`datalab-jupyter` connects the datalab Tools framework to JupyterHub and to the
Jupyter user environments started by that Hub. It is one Python distribution
with independently installable components; it is not a JupyterLab frontend
extension.

The datalab documentation describes the generic
[standalone-tool contract](https://docs.datalab-org.io/en/latest/plugin-development/standalone-tool-plugin/).
This repository owns the Jupyter-specific installation, deployment, and operations guidance.

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
temporary non-production deployment reached by plain HTTP, the Datalab API process may explicitly
set `DATALAB_JUPYTER_ALLOW_INSECURE_HTTP=true`. The plugin reads this setting when it validates the
public URL. This safety override is disabled by default and must not be used in production.
It does not disable the launch-code exchange or replace `DATALAB_JUPYTER_CLIENT_ID` and
`DATALAB_JUPYTER_CLIENT_SECRET`; both credentials remain required.

## Deploy the managed JupyterHub with Datalab Compose

Installing the Python plugin registers the tool in Datalab, but it does not start JupyterHub. A
deployment that uses the managed Hub must also combine Datalab's Compose file with the companion
file provided in this repository:

[`deployment/docker-compose.datalab.yml`](deployment/docker-compose.datalab.yml)

For checkouts of `datalab` and `datalab-jupyter` beside one another, run from the Datalab checkout:

```shell
export DATALAB_JUPYTER_IMAGE_REPOSITORY='registry.example/datalab-jupyter'
export DATALAB_JUPYTER_IMAGE_DIGEST='sha256:<verified-digest>'
docker compose \
  --file docker-compose.yml \
  --file ../datalab-jupyter/deployment/docker-compose.datalab.yml \
  --profile prod up --detach
```

The example values are placeholders, not a usable image reference. Set the repository and the
`sha256:...` digest reported by a trusted registry after reviewing and building the selected source
revision. Compose joins them as `repository@digest`, so both values are required for the production
path; Docker rejects a malformed digest reference before starting the container. The Hub image
receives the Docker socket so that DockerSpawner can create user containers; possession of that
socket is effectively host-root access. A tag alone, including a version tag or `latest`, is
mutable and is not sufficient for production.

By default, spawned user containers use the same image as the Hub. Set
`DATALAB_JUPYTER_SINGLEUSER_IMAGE` only when a separate user image is required, and pin that image
by digest in production as well.

The companion file adds JupyterHub, its persistent data volume, and the private `tools` network. It
also gives the Datalab API the `datalab-api` network alias used by JupyterHub. It does not install
the API plugin; keep `datalab-jupyter[plugin]` in Datalab's `plugins.toml` as described above.

Provide the shared client ID and secret to both the API and JupyterHub. The Hub's
`DATALAB_JUPYTER_API_URL` must include Datalab's configured API root when it is not `/`. For
example, use `http://datalab-api:5001/api` when `PYDATALAB_ROOT_PATH=/api`.

The companion file deliberately does not enable or forward the unsafe HTTP option. If a trusted
non-production deployment intentionally exposes Jupyter over plain HTTP, add a deployment-specific
Compose override:

```yaml
services:
  api:
    environment:
      - DATALAB_JUPYTER_ALLOW_INSECURE_HTTP
```

Then set `DATALAB_JUPYTER_ALLOW_INSECURE_HTTP=true` in the environment used by Compose. HTTPS or a
loopback URL needs no such override.

Use the same Compose files for later operations, including shutdown:

```shell
docker compose \
  --file docker-compose.yml \
  --file ../datalab-jupyter/deployment/docker-compose.datalab.yml \
  --profile dev down
```

If the companion file is omitted from `down`, Compose does not know about the Hub service and may
leave it running with the tools network still in use. Production and development profiles are
alternatives and should not be run simultaneously. Docker Compose 2.20 or newer is required for
the optional health-based API dependencies.

The managed Hub binds to `127.0.0.1:8000` by default. Change this with
`DATALAB_JUPYTER_BIND_ADDRESS` and `DATALAB_JUPYTER_PORT`. For loopback development, its browser URL
is normally `http://localhost:8000/jupyter/`.

In production, set `PYDATALAB_APP_URL` to the canonical frontend URL and proxy its `/jupyter/` path
to the Hub, or set `DATALAB_JUPYTER_PUBLIC_URL` to another browser-facing HTTPS URL. The reverse
proxy must preserve the path prefix and forwarded host and protocol information, support WebSocket
upgrades and long-lived kernel connections, terminate TLS, and omit or redact
`datalab_launch_code` values from access logs.

### Security, networking, and storage

The API, Hub, and spawned user servers share a dedicated tools network. The API uses the
`datalab-api` alias, so notebooks can call it without joining the database network. MongoDB is not
attached to the tools network; notebooks access data through the Datalab API and its current-user
permission checks.

DockerSpawner requires the Docker socket to create user containers. Only the reviewed Hub image
receives it. User containers receive no Docker socket, MongoDB credentials, Flask secrets, host
bind mounts, or shared client secret. Each receives only its user's temporary tool token,
current-user snapshot, API URL, and persistent work volume.

The managed defaults are 2 CPU cores and 4 GiB of memory per user server, shutdown after one hour
of inactivity, and a maximum server age of 24 hours. Override these with
`DATALAB_JUPYTER_CPU_LIMIT`, `DATALAB_JUPYTER_MEM_LIMIT`, `DATALAB_JUPYTER_START_TIMEOUT`,
`DATALAB_JUPYTER_IDLE_TIMEOUT`, and `DATALAB_JUPYTER_MAX_AGE`.

Hub state and encrypted authentication data use a persistent volume. Stopping a user server
removes its disposable container while retaining its work volume. Administrators should define
storage quotas and a volume-retention policy suitable for their deployment.

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

Set `DATALAB_JUPYTER_EXTERNAL_URL` in the Datalab API and run the normal Datalab Compose file
without this repository's companion file. The external administrator must isolate notebook
containers from Datalab's database and server secrets. New Datalab users are created dynamically
in JupyterHub on their first successful launch; a pre-provisioned user list is not required.

## Managed image

Before a production deployment, review an exact source revision and its Dockerfile, build it in a
trusted environment, publish it to a trusted registry, and record the registry-reported digest.
Configure the companion Compose file with that complete digest reference; it does not fetch or
build source code. Supply the reference as `DATALAB_JUPYTER_IMAGE_REPOSITORY` and the
registry-reported `DATALAB_JUPYTER_IMAGE_DIGEST`; Compose constructs the immutable reference.

For local development, build only from the explicitly selected checkout, then configure Compose to
use that local image:

```shell
cd /path/to/datalab-jupyter
docker build --tag datalab-jupyter:dev .

cd /path/to/datalab
export DATALAB_JUPYTER_UNPINNED_IMAGE=datalab-jupyter:dev
docker compose \
  --file docker-compose.yml \
  --file ../datalab-jupyter/deployment/docker-compose.datalab.yml \
  --profile dev up --detach --build
```

`DATALAB_JUPYTER_UNPINNED_IMAGE` is an explicit non-production escape hatch: it acknowledges that
the selected image is mutable and will receive the Docker socket. Never set it in production. The
production repository and digest take precedence if both paths are configured. The remaining
`--build` builds services that still have local build definitions in Datalab's main Compose file;
the JupyterHub service itself is image-only.

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

## Notebook environment

Each Python notebook and Jupyter console preloads:

```python
datalab  # authenticated datalab_api.DatalabClient
current_user  # launch-time identity, role, and group snapshot
```

JupyterLab also contributes **Open in notebook** to the selected-items menus for Samples,
Inventory, Equipment, and items within a collection. Selecting 1–20 rows creates a notebook with a
dedicated kernel and a visible initialization cell. Datalab passes only immutable refcodes; the
cell retrieves the current item dictionaries through the permission-aware API and defines:

```python
selected_item_refcodes  # ordered immutable refcodes from the table
selected_items  # accessible item dictionaries
selected_item_errors  # refcodes that could not be loaded
```

The cell runs automatically once when the notebook is created and remains editable and rerunnable.
It is not rerun automatically after a kernel restart. A normal JupyterLab launch from the Tools
menu does not create a notebook or define these selection-specific variables.

The managed image includes SciPy, pandas, Matplotlib, seaborn, ipywidgets, lmfit, uncertainties,
Pint, openpyxl, h5py, and tqdm. NumPy is installed as a dependency and constrained for
compatibility with the pinned `datalab-api` client.

## Development

```shell
uv sync --extra plugin --group plugin-dev --dev
uv sync --extra hub --extra server --dev
uv run pre-commit run --all-files
uv build
```
