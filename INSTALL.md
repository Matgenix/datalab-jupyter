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

See the README for configuration and managed-image instructions.
