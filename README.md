# datalab-jupyter

`datalab-jupyter` connects datalab's Tools framework to JupyterHub and to the
Jupyter user environments started by that Hub. It is not a JupyterLab frontend
extension.

- **Users:** follow [Using JupyterLab](#using-jupyterlab) below.
- **Deployment administrators:** follow [INSTALL.md](INSTALL.md) for the managed
  Compose path, external JupyterHub alternative, configuration, upgrades, and
  security boundaries.
- **Developers:** see [Development](#development) and datalab's generic
  [standalone tool guide](https://docs.datalab-org.io/en/latest/plugin-development/standalone-tool-plugin/).

## Using JupyterLab

### Open a working environment

Sign in to datalab, click **Tools**, and choose **JupyterLab**. The Hub starts or
replaces your user server and opens JupyterLab. Each Python notebook and Jupyter
console preloads:

```python
datalab  # authenticated datalab_api.DatalabClient
current_user  # launch-time identity, effective role, and group snapshot
```

Use `datalab` to access only the data available to your datalab account. The
delegated role is always `user`, even when a datalab administrator launches the
tool.

### Open selected items in a notebook

1. Select 1–20 rows in Samples, Inventory, Equipment, or the items in a
   collection.
2. Choose **Open in notebook** from the selected-items menu.
3. Wait for JupyterLab to create a notebook with a dedicated kernel and run its
   visible initialization cell.

The cell defines:

```python
selected_item_refcodes  # ordered immutable refcodes from the table
selected_items  # item dictionaries accessible to the current user
selected_item_errors  # refcodes that could not be loaded
```

You can edit and rerun the cell. It does not run automatically after a kernel
restart. A normal launch from **Tools** does not create a notebook or define
these selection variables.

### Sessions and files

Launching JupyterLab again replaces your existing user server so the new server
receives a fresh temporary datalab credential. Files in your persistent work
volume survive; running kernels, terminals, processes, and unsaved in-memory
state stop. Save work before relaunching.

The managed image includes SciPy, pandas, Matplotlib, seaborn, ipywidgets,
lmfit, uncertainties, Pint, openpyxl, h5py, tqdm, and a compatible NumPy.
Deployment administrators may provide a different single-user image.

If a datalab request reports HTTP 401, save your files and relaunch JupyterLab
from datalab. Contact the deployment administrator if JupyterLab is absent from
the complete Tools catalog or the Hub fails to start.

## Integration overview

The distribution has three independently installable components:

- `datalab-jupyter[plugin]`: the concrete `jupyter` tool provider in the datalab
  API environment;
- `datalab-jupyter[hub]`: the datalab authenticator in JupyterHub; and
- `datalab-jupyter[server]`: Jupyter Server and IPython integration in each user
  environment.

The provider issues a single-use launch grant. The Hub exchanges it with shared
client credentials, stores the temporary datalab token in encrypted JupyterHub
authentication state, and injects it only into the matching user server. Browser
handoffs carry launch codes in URL fragments and submit them through
same-origin, XSRF-protected POST requests.

Provider, Hub, and server versions must be upgraded together. Installation does
not itself start JupyterHub; the deployment chooses either the managed companion
Compose file or a compatible external Hub. The complete operational and
security contract is in [INSTALL.md](INSTALL.md).

## Development

Install and test the provider separately when only the datalab integration is
needed:

```shell
uv sync --extra plugin --group plugin-dev --dev
```

Install the Hub and user-server integrations together when testing the full
handoff:

```shell
uv sync --extra hub --extra server --dev
```

Run the repository checks and build the distribution:

```shell
uv run pytest
uv run pre-commit run --all-files
uv build
```

The `plugin-dev` dependency group supplies the datalab core API for isolated
provider development. Do not release one component independently: browser
handoff and user-server lifecycle changes require an atomic version across the
provider, Hub, and server extras.
