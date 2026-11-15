# Changelog

## Unreleased

- Require managed deployments to provide a reviewed, digest-pinned Jupyter image instead of
  building the mutable remote default branch. The companion Compose configuration now fails
  closed unless both production image components are set or the explicit, non-production
  `DATALAB_JUPYTER_UNPINNED_IMAGE` escape hatch is used.
- Extract the datalab Jupyter tool, JupyterHub authenticator, Jupyter Server
  extension, IPython integration, and managed image into one distribution.
