import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from tornado import web

from datalab_jupyter.hub import authenticator as authenticator_module
from datalab_jupyter.hub.authenticator import (
    REUSE_CANDIDATE_HEADER,
    DatalabAuthenticator,
    _credential_fingerprint,
    _ExchangeResponse,
    _is_expired,
)


def valid_payload():
    return {
        "user_id": "0123456789abcdef01234567",
        "display_name": "Example User",
        "role": "user",
        "group_ids": ["group-one"],
        "tool_access_token": "temporary-secret",
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    }


class ExchangeResponseTests(TestCase):
    def test_exchange_response_validation(self):
        response = _ExchangeResponse.from_payload(valid_payload())

        self.assertEqual(response.group_ids, ("group-one",))
        self.assertFalse(response.session_reused)
        self.assertFalse(_is_expired(response.expires_at))

    def test_exchange_response_rejects_invalid_fields(self):
        invalid_values = [
            ("user_id", ""),
            ("group_ids", [1]),
            ("tool_access_token", ""),
            ("expires_at", "not-a-date"),
            ("session_reused", 1),
            ("notebook_launch_code", ""),
        ]
        for field, value in invalid_values:
            with self.subTest(field=field):
                payload = valid_payload()
                payload[field] = value
                with self.assertRaises(ValueError):
                    _ExchangeResponse.from_payload(payload)

    def test_exchange_response_accepts_strict_optional_fields(self):
        payload = valid_payload()
        payload.update(session_reused=True, notebook_launch_code="selection-code")

        response = _ExchangeResponse.from_payload(payload)

        self.assertTrue(response.session_reused)
        self.assertEqual(response.notebook_launch_code, "selection-code")

        payload["unsupported"] = True
        with self.assertRaises(ValueError):
            _ExchangeResponse.from_payload(payload)


class _Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _AsyncClient:
    def __init__(self, response, requests, **_kwargs):
        self.response = response
        self.requests = requests

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return self.response


class AuthenticatorTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.authenticator = DatalabAuthenticator(
            api_url="http://datalab-api:5001/",
            client_id="jupyter-client",
            client_secret="integration-secret",  # noqa: S106
        )

    async def test_authenticate_sends_candidate_and_keeps_selection_request_local(self):
        payload = valid_payload()
        payload.update(session_reused=True, notebook_launch_code="selection-code")
        requests = []
        handler = SimpleNamespace(
            _datalab_launch_code="launch-code",
            _datalab_candidate_token=payload["tool_access_token"],
        )

        with patch.object(
            authenticator_module.httpx,
            "AsyncClient",
            side_effect=lambda **kwargs: _AsyncClient(_Response(payload), requests, **kwargs),
        ):
            authenticated = await self.authenticator.authenticate(handler)

        self.assertEqual(len(requests), 1)
        request_url, request_kwargs = requests[0]
        self.assertTrue(request_url.endswith("/v0.1/tools/plugins/jupyter/exchange"))
        self.assertEqual(
            request_kwargs["headers"],
            {REUSE_CANDIDATE_HEADER: payload["tool_access_token"]},
        )
        self.assertEqual(authenticated["name"], f"datalab-{payload['user_id']}")
        self.assertNotIn("notebook_launch_code", authenticated["auth_state"])
        self.assertEqual(handler._datalab_exchange.notebook_launch_code, "selection-code")

    async def test_authenticate_rejects_a_false_reuse_claim(self):
        payload = valid_payload()
        payload["session_reused"] = True
        handler = SimpleNamespace(
            _datalab_launch_code="launch-code",
            _datalab_candidate_token="different-token",  # noqa: S106
        )
        with patch.object(
            authenticator_module.httpx,
            "AsyncClient",
            side_effect=lambda **kwargs: _AsyncClient(_Response(payload), [], **kwargs),
        ):
            with self.assertRaises(web.HTTPError) as raised:
                await self.authenticator.authenticate(handler)

        self.assertEqual(raised.exception.status_code, 502)

    async def test_authenticate_applies_a_wall_clock_exchange_deadline(self):
        client = _AsyncClient(_Response(valid_payload()), [])

        async def slow_post(*_args, **_kwargs):
            await asyncio.Event().wait()

        client.post = slow_post
        handler = SimpleNamespace(_datalab_launch_code="launch-code")
        with (
            patch.object(authenticator_module, "_EXCHANGE_TIMEOUT_SECONDS", 0.01),
            patch.object(authenticator_module.httpx, "AsyncClient", return_value=client),
        ):
            with self.assertRaises(web.HTTPError) as raised:
                await self.authenticator.authenticate(handler)

        self.assertEqual(raised.exception.status_code, 503)

    async def test_spawn_hooks_record_and_clear_running_server_provenance(self):
        auth_state = {
            "tool_access_token": "delegated-token",
            "api_url": "http://datalab-api:5001",
            "current_user": {"id": "user-id"},
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        }
        user = SimpleNamespace(get_auth_state=lambda: None)

        async def get_auth_state():
            return auth_state

        user.get_auth_state = get_auth_state
        spawner = SimpleNamespace(environment={})

        await self.authenticator.pre_spawn_start(user, spawner)

        self.assertEqual(spawner.environment["DATALAB_API_KEY"], "delegated-token")
        self.assertEqual(
            spawner._datalab_credential_fingerprint,
            _credential_fingerprint("delegated-token", "http://datalab-api:5001"),
        )
        await self.authenticator.post_spawn_stop(user, spawner)
        self.assertFalse(hasattr(spawner, "_datalab_credential_fingerprint"))
