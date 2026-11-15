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

Installing the plugin does not itself start JupyterHub. For the managed deployment, combine
Datalab's Compose file with
[`deployment/docker-compose.datalab.yml`](deployment/docker-compose.datalab.yml). See the README
for the complete Compose command, configuration, unsafe-HTTP warning, and managed-image
instructions. Production use requires `DATALAB_JUPYTER_IMAGE_REPOSITORY` and
`DATALAB_JUPYTER_IMAGE_DIGEST` to construct a reviewed image's immutable
`repository@sha256:digest` reference; the companion file does not build remote source code. Local
development may instead explicitly set `DATALAB_JUPYTER_UNPINNED_IMAGE` after building the selected
checkout.
