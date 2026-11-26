# Changelog

## Unreleased

- Replace the directly exchangeable browser launch URL with a two-code,
  browser-authorized handoff bound to an RFC 7636 `S256` verifier cookie, the
  confidential provider client, and any existing immutable Hub subject. Require
  `DATALAB_JUPYTER_BROWSER_API_URL`, an explicit trusted Hub public URL, and a
  coordinated core/provider/Hub rollout with no raw-code compatibility path.
- Offer an explicit, five-minute-bound confirmation when a stored datalab
  credential has one hour or less remaining. Successful renewal advances its
  authoritative expiry without changing the token or restarting a compatible
  server; capped or invalid credentials retain the controlled replacement path.
- Open every successful launch in a distinct native JupyterLab workspace. Give
  each selected-items launch a randomly named notebook, dedicated kernel, and
  Jupyter session, with request-owned cleanup after fatal partial failures.
- Reuse a ready Jupyter user server when datalab returns the exact delegated
  token already injected into it with at least ten minutes remaining, preserving
  kernels and terminals; retain conservative one-time replacement for fresh,
  mismatched, near-expiry, or unverifiable credentials.
- Serialize Hub-local credential exchange and per-user lifecycle reconciliation,
  and keep selected-items handoff codes request-local instead of shared auth state.
- Harden managed user containers with a validated PID limit, a complete Linux capability drop,
  and `no-new-privileges`. Document that managed Compose is a trusted-user profile whose shared
  bridge and named-volume backing storage do not provide hostile multi-tenant isolation.
- Keep Hub and selected-items launch credentials out of HTTP request targets by
  moving both handoffs to same-origin, XSRF-protected fragment-to-POST
  bootstraps. All three integration components now require an atomic upgrade.
- Replace an existing user server after every successful relaunch so its
  environment receives the newly delegated datalab token, while preserving
  files in the persistent work volume. Release this before or atomically with
  datalab's revoke-on-relaunch delegated-session policy.
- Use the explicitly service-only launch-code exchange API after authenticating
  the JupyterHub client, avoiding accidental browser-style cross-user exchange.
- Require managed deployments to provide a reviewed, digest-pinned Jupyter image instead of
  building the mutable remote default branch. The companion Compose configuration now fails
  closed unless both production image components are set or the explicit, non-production
  `DATALAB_JUPYTER_UNPINNED_IMAGE` escape hatch is used.
- Extract the datalab Jupyter tool, JupyterHub authenticator, Jupyter Server
  extension, IPython integration, and managed image into one distribution.
