# Installation and operations

This guide is for deployment administrators. The standard deployment uses the
companion Docker Compose file to run a managed JupyterHub beside datalab. Use the
[external Hub](#external-jupyterhub) path only when an existing platform owns
spawning, storage, isolation, and lifecycle management.

`datalab-jupyter` is trusted code. Review one exact source revision, its
dependencies and Dockerfile, and the resulting images before deployment. Install
the same version of its provider, Hub, and server components and upgrade them
atomically.

## Managed JupyterHub with datalab Compose

### 1. Prepare a reviewed image

Build the selected source revision in a trusted environment, publish it to a
trusted registry, and record the registry-reported digest. Production uses an
immutable `repository@sha256:digest` reference:

```shell
export DATALAB_JUPYTER_IMAGE_REPOSITORY='registry.example/datalab-jupyter'
export DATALAB_JUPYTER_IMAGE_DIGEST='sha256:<registry-reported-digest>'
```

The placeholders are not usable values. A version tag alone, including
`latest`, is mutable and is not sufficient. By default the Hub and user servers
use this same image. Set `DATALAB_JUPYTER_SINGLEUSER_IMAGE` only when a separate,
also digest-pinned user image is required.

### 2. Install the provider in datalab

Add the provider extra to the datalab checkout's root `plugins.toml`, preserving
existing entries and pinning the same reviewed release:

```toml
dependencies = ["datalab-jupyter[plugin]"]

[tool.uv.sources]
datalab-jupyter = { git = "https://github.com/Matgenix/datalab-jupyter.git", rev = "<reviewed-tag-or-commit>" }
```

Run `uv run invoke dev.install` from `pydatalab/`. This registers the tool; it
does not start JupyterHub. Use datalab's generic
[plugin operations guide](https://docs.datalab-org.io/en/latest/plugins/#installing-and-maintaining-plugins)
for provider rollback, removal, and `TOOLS.DISABLED` or `TOOLS.ORDER`.

### 3. Configure the shared integration

Give the API and Hub the same client ID and secret. The secret must contain at
least 32 characters; generate and store it with the deployment's secret manager.

```shell
export DATALAB_JUPYTER_CLIENT_ID='datalab-jupyter'
export DATALAB_JUPYTER_CLIENT_SECRET='<secret-with-at-least-32-characters>'
export PYDATALAB_APP_URL='https://datalab.example.org'
```

`PYDATALAB_APP_URL` is the canonical browser-facing datalab URL. With no URL
override, the managed provider derives `/jupyter/` from it. Set
`DATALAB_JUPYTER_PUBLIC_URL` only when the managed Hub uses another public HTTPS
URL.

The Hub reaches the API at `http://datalab-api:5001` by default. If datalab has
a non-root `PYDATALAB_ROOT_PATH`, include it in `DATALAB_JUPYTER_API_URL`; for
example, use `http://datalab-api:5001/api` for `/api`.

### 4. Start the deployment

For checkouts of `datalab` and `datalab-jupyter` beside one another, run from
the datalab checkout:

```shell
docker compose \
  --file docker-compose.yml \
  --file ../datalab-jupyter/deployment/docker-compose.datalab.yml \
  --profile prod up --detach
```

Adjust the companion path if the repositories are laid out differently. Docker
Compose 2.20 or newer is required. The companion file adds the Hub, persistent
Hub state, and the private `tools` network, and gives the datalab API its
`datalab-api` alias. The normal datalab Compose deployment creates none of these
resources when this file is omitted.

### 5. Configure the reverse proxy

The managed Hub listens on `127.0.0.1:8000` and serves `/jupyter/` by default.
Proxy the public `/jupyter/` path to it, or set
`DATALAB_JUPYTER_PUBLIC_URL` to another browser-facing HTTPS URL. The proxy must:

- preserve the path prefix and forwarded host and protocol;
- support WebSocket upgrades and long-lived kernel connections;
- terminate TLS; and
- disable request-body logging.

Launch credentials are removed from URL fragments and submitted in same-origin
POST bodies. Verify through the real proxy that access logs contain only the
credential-free `/hub/datalab-login` and
`/user/<name>/datalab/open-selected` paths and no request bodies.

### 6. Verify as a normal user

Confirm that a non-administrator can launch **JupyterLab**, use the preloaded
`datalab` client, and create a notebook with **Open in notebook**. Once the
delegated token enters its final hour, relaunch, choose **Extend Datalab access
and open JupyterLab**, and verify that the token, server, kernel state, running
terminal, and persistent files remain while the authoritative expiry advances.
Verify that **Continue without extending** reuses above the ten-minute minimum
and rotates/restarts below it. A hard-capped, revoked, or unverifiable credential
must also replace the server once while preserving persistent files. Confirm idle
and maximum-age culling still apply, and that the Hub and user containers cannot
reach MongoDB or read datalab server secrets.
If the confirmation expires or reports changed access, launch JupyterLab again
from datalab and repeat the check.

## Routine operations

Use the same Compose files and profile for every operation. For example:

```shell
docker compose \
  --file docker-compose.yml \
  --file ../datalab-jupyter/deployment/docker-compose.datalab.yml \
  --profile prod down
```

Omitting the companion file from `down` can leave the Hub running and the tools
network in use. Production and development profiles are alternatives; do not run
both simultaneously.

### Upgrade and rollback

Deploy datalab's `REUSE_OR_REPLACE` policy and explicit-renewal core support
before this release. Then upgrade the provider package, Hub image, and user-server
integration atomically. The browser handoff has no query-string compatibility
path. An older Hub sends no candidate and therefore continues to replace safely;
a provider response without result flags is also treated as a replacement.

For an upgrade, review a new source revision, publish and pin its image digest,
pin the matching provider release in `plugins.toml`, rebuild datalab, and recreate
the combined deployment. Verify the launch and relaunch tests above. For
rollback, restore both previous pins and recreate all components together.
Roll back the Jupyter components before rolling back datalab core, and allow
outstanding five-minute launch grants to expire. Rollback does not shorten a
credential whose expiry was already renewed; revoke it explicitly if needed.

### Removal

Stop the combined deployment with the companion file, then remove the
`datalab-jupyter[plugin]` dependency and source from `plugins.toml`, rebuild
datalab, and remove the Jupyter proxy route and credentials. Named Hub and user
volumes are retained unless an administrator explicitly deletes them. Back up
required notebooks first and apply the deployment's retention policy before
removing any volume.

### Resource and lifetime settings

Managed user-server defaults are 2 CPU cores, 4 GiB memory, at most 512
processes, shutdown after one hour idle, and a maximum age of 24 hours. Override
them only after representative scientific-kernel testing:

Delegated datalab credentials normally use a 24-hour rolling renewal horizon
with a seven-day absolute age cap. Those limits are independent of the Hub login
cookie, the one-hour idle timeout, and `DATALAB_JUPYTER_MAX_AGE`. Increasing the
server maximum age for multi-day computation requires a separate resource and
security review; credential renewal alone does not keep a server alive.

Every launch receives a native JupyterLab workspace, but all of a user's
workspaces run in the same default single-user server and persistent volume.
Selected-items launches create a dedicated notebook, kernel, and Jupyter session
inside that shared server.

| Object | Per launch? | Isolation model |
|---|---:|---|
| Single-user server/container | No — one default server per datalab user | Shared process and resource boundary |
| Persistent volume/files | No | Shared |
| JupyterLab workspace | Yes | Browser layout only |
| Selected notebook/kernel/session | Yes, for selected-items launches | Separate kernel; shared server, resources, and token |

Users can inspect and stop accumulated kernels and terminals with JupyterLab's
**Running Terminals and Kernels** panel. The integration does not discover or
clean up old workspaces, impose per-workspace process quotas, or make two views
of the same notebook independent.

| Setting | Default | Purpose |
|---|---:|---|
| `DATALAB_JUPYTER_CPU_LIMIT` | `2` | CPU cores per user server |
| `DATALAB_JUPYTER_MEM_LIMIT` | `4G` | Memory per user server |
| `DATALAB_JUPYTER_PIDS_LIMIT` | `512` | Process limit; valid range 1–65,536 |
| `DATALAB_JUPYTER_START_TIMEOUT` | `120` | Server startup timeout in seconds |
| `DATALAB_JUPYTER_IDLE_TIMEOUT` | `3600` | Idle shutdown in seconds |
| `DATALAB_JUPYTER_MAX_AGE` | `86400` | Maximum server lifetime in seconds |

The Hub bind can be changed with `DATALAB_JUPYTER_BIND_ADDRESS` and
`DATALAB_JUPYTER_PORT`. `DATALAB_JUPYTER_CONNECT_IP` controls the Hub address
used by spawned containers. `DATALAB_JUPYTER_DOCKER_NETWORK` and
`DATALAB_JUPYTER_VOLUME_PREFIX` normally use deployment-specific Compose
defaults and should change only when integrating with an existing layout.

### Storage and retention

Hub state and encrypted authentication data use a persistent volume. Stopping a
user server removes its disposable container but retains its work volume.
Docker's local named-volume driver has no portable per-volume quota. Before
production use:

1. Put user-volume storage on a dedicated size-bounded or quota-backed
   filesystem or volume driver.
2. Monitor free space and alert before exhaustion.
3. Define backup and volume-retention policies, including removal for departed
   users.

This bounds total deployment storage; it does not prevent one user from filling
the shared backing store. Hard per-user quotas require external storage or an
orchestrator that can enforce a quota per user.

## Security boundaries

The API, Hub, and user servers share the dedicated tools network. MongoDB is not
attached; notebooks access data through the datalab API. User containers receive
only their temporary tool token, current-user snapshot, API URL, and work
volume. They receive no Docker socket, MongoDB credentials, Flask secrets, host
bind mounts, or shared client secret.

The managed image runs user containers as `1000:100`, drops all Linux
capabilities, enables `no-new-privileges`, and applies the configured resource
and lifetime limits. The Hub alone receives the Docker socket for DockerSpawner;
that socket grants effective host-root access, so only a reviewed digest-pinned
Hub image may receive it.

The managed profile is for authenticated users in one organizational trust
domain. User servers share a bridge network and do not have per-user network
policy. It is not a hostile multi-tenant isolation boundary. Mutually untrusted
users require an external JupyterHub or orchestrator with enforceable per-user
storage quotas and NetworkPolicy or equivalent firewall controls. Production
approval must record acceptance of the remaining shared-storage,
shared-network, and Docker-socket risks.

Launch codes and temporary tokens must never be stored in URLs, notebooks,
files, analytics, or logs. Browser fragments are removed before same-origin POST
exchange, but proxies must still avoid request-body logging. The near-expiry
confirmation is XSRF-protected and binds the current Hub user, candidate token,
API URL, stored expiry, and launch code for five minutes. It does not prove that
a running server received that token; process-local provenance is checked later.

Reuse coordination is local to one Hub process. The exchange is serialized, and
each live Hub user has a separate lifecycle lock. Running-server credential
provenance is deliberately process-local, so the first launch after a Hub restart
conservatively replaces a surviving server. Idle-culler and administrator actions
remain outside these locks. Renewal extends every copy of the bearer token, while
account status, provider availability, permissions, revocation, and expiry remain
live datalab API checks. Data already loaded into memory or the persistent volume
cannot be withdrawn retroactively.

## External JupyterHub

Use an external Hub when another platform owns the spawner, TLS, storage,
resource controls, culling, and tenant isolation.

Install `datalab-jupyter[hub]` in the Hub environment and
`datalab-jupyter[server]` in the single-user image. Configure the Hub:

```shell
pip install 'datalab-jupyter[hub]==<reviewed-version>'
# In the single-user image, install the same version:
pip install 'datalab-jupyter[server]==<reviewed-version>'
```

The `server` extra integrates with Jupyter Server but does not install the
JupyterLab user interface. The single-user image must also provide a compatible
JupyterLab installation (the managed image uses JupyterLab 4.6.2). Verify that
`/lab/workspaces/<workspace-id>/tree/<notebook>` URLs open correctly in that
image before deployment.

Then configure the Hub:

```python
from datalab_jupyter.hub import DatalabAuthenticator

c.JupyterHub.authenticator_class = DatalabAuthenticator
c.DatalabAuthenticator.api_url = "https://api.example.org"
c.DatalabAuthenticator.client_id = "datalab-jupyter"
c.DatalabAuthenticator.client_secret = "<the-same-deployment-secret>"
c.DatalabAuthenticator.enable_auth_state = True
c.DatalabAuthenticator.allow_all = True
c.DatalabAuthenticator.auto_login = True
```

Set a persistent `JUPYTERHUB_CRYPT_KEY`, use
`datalab-jupyter-singleuser` as the spawner command, and keep user-server
lifetimes within the delegated datalab session lifetime. Configure the provider
with `DATALAB_JUPYTER_EXTERNAL_URL=<public-https-hub-url>` and run datalab
without the companion Compose file.

The external deployment must preserve the `DATALAB_API_KEY`, `DATALAB_API_URL`,
and `DATALAB_CURRENT_USER_JSON` values written by the authenticator's pre-spawn
hook. It may retain a ready default server only when the authenticator verifies
matching process-local credential provenance; otherwise it replaces that server
once. It must also isolate user environments from datalab's database and secrets
and meet the proxy logging requirements above. New datalab users are created in
the Hub on first successful launch; no pre-provisioned user list is required.

## Local development and insecure HTTP

For local development, build the explicitly selected checkout and opt into its
mutable image:

```shell
cd /path/to/datalab-jupyter
docker build --tag datalab-jupyter:dev .

cd /path/to/datalab
export DATALAB_JUPYTER_UNPINNED_IMAGE='datalab-jupyter:dev'
docker compose \
  --file docker-compose.yml \
  --file ../datalab-jupyter/deployment/docker-compose.datalab.yml \
  --profile dev up --detach --build
```

`DATALAB_JUPYTER_UNPINNED_IMAGE` explicitly selects a mutable image that will
receive the Docker socket. Never set it in production. A configured production
repository and digest take precedence.

For a `datalab-jupyter` checkout inside the datalab repository's `dev-repos/`,
use an editable provider source while developing:

```toml
dependencies = ["datalab-jupyter[plugin]"]

[tool.uv.sources]
datalab-jupyter = { path = "dev-repos/datalab-jupyter", editable = true }
```

Run `uv run invoke dev.install` from `pydatalab/` after provider changes and
restart or rebuild the datalab API.

HTTPS is required except for loopback URLs. For a trusted, temporary
non-production deployment that must use another plain-HTTP address, set
`PYDATALAB_TOOLS__ALLOW_INSECURE_HTTP=true` in the Datalab API process. The
companion file intentionally does not forward it; add an explicit local
override:

```yaml
services:
  api:
    environment:
      - PYDATALAB_TOOLS__ALLOW_INSECURE_HTTP
```

This generic Datalab setting allows plain-HTTP launch URLs from every installed
standalone tool. It does not remove authentication, authorization, the launch
exchange, or shared client credentials, but HTTP can expose launch codes,
tokens, and user data and must never be used in production. Existing local
preview environment and override files using the former Jupyter-specific name
must be updated when moving to these coordinated revisions.
