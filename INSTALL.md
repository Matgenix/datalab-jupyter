# Installation

Choose only the components needed by the target process:

```shell
pip install 'datalab-jupyter[plugin]'  # datalab API
pip install 'datalab-jupyter[hub]'     # JupyterHub
pip install 'datalab-jupyter[server]'  # Jupyter user image
```

The Hub and datalab provider must use the same client ID and secret. The Hub
must be able to reach the configured datalab API URL, while users' browsers must
be able to reach the configured public or external Hub URL.

Install matching provider, Hub, and server component versions in one atomic
upgrade. Browser handoff credentials travel in URL fragments and are submitted
by same-origin, XSRF-protected bootstrap pages; query-string handoffs are not
supported. Keep HTTPS enabled, disable request-body logging at every proxy, and
confirm that access logs contain only the credential-free login and
selected-items paths.

Each successful relaunch replaces an existing user server after authentication
so it receives the fresh delegated token. Persistent work-volume files survive;
running kernels, terminals, processes, and unsaved in-memory state are stopped.
External Hub deployments must retain this lifecycle behavior.

Deploy this restart behavior before or atomically with datalab's
revoke-on-relaunch delegated-session policy. Deploying the core policy first
would invalidate the token in every reused Jupyter container without replacing
that container.

Installing the plugin does not itself start JupyterHub. For the managed deployment, combine
Datalab's Compose file with
[`deployment/docker-compose.datalab.yml`](deployment/docker-compose.datalab.yml). See the README
for the complete Compose command, configuration, unsafe-HTTP warning, and managed-image
instructions. Production use requires `DATALAB_JUPYTER_IMAGE_REPOSITORY` and
`DATALAB_JUPYTER_IMAGE_DIGEST` to construct a reviewed image's immutable
`repository@sha256:digest` reference; the companion file does not build remote source code. Local
development may instead explicitly set `DATALAB_JUPYTER_UNPINNED_IMAGE` after building the selected
checkout.

The managed profile runs user containers as a non-root user with all Linux capabilities dropped,
`no-new-privileges`, CPU and memory limits, and a PID limit of 512 by default. Set
`DATALAB_JUPYTER_PIDS_LIMIT` only to a validated value from 1 through 65,536. Before production
use, place Docker's user-volume storage on bounded or quota-backed storage and monitor free space;
the local named-volume driver does not enforce per-user quotas.

Managed Compose is a single organizational trust domain: user servers share its tools bridge and
do not have per-user network policy. It is not a hostile multi-tenant isolation boundary. Use an
external JupyterHub or orchestrator with per-user storage quotas and network policy for mutually
untrusted users. Review and explicitly accept the managed profile's remaining shared-storage,
shared-network, and Docker-socket risks before deployment.
