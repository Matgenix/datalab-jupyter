import importlib.util
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import TestCase, skipUnless
from unittest.mock import call, create_autospec, patch

TOOLS_API_AVAILABLE = (
    importlib.util.find_spec("pydatalab") is not None
    and importlib.util.find_spec("pydatalab.tools") is not None
)

if TOOLS_API_AVAILABLE:
    from flask import Flask
    from pydatalab.tools import ToolLaunchGrantIssuer, ToolSessionPolicy

    from datalab_jupyter.plugin import provider as provider_module
    from datalab_jupyter.plugin.provider import JUPYTER_BLUEPRINT, JupyterToolProvider


@skipUnless(TOOLS_API_AVAILABLE, "the datalab tool API is not installed")
class ProviderTests(TestCase):
    def test_provider_metadata_matches_the_tool_contract(self):
        metadata = JupyterToolProvider.metadata

        self.assertEqual(JupyterToolProvider.id, "jupyter")
        self.assertEqual(metadata.ui.kind, "standalone")
        self.assertEqual(len(metadata.launch_actions), 1)

        action = metadata.launch_actions[0]
        self.assertEqual(action.id, "open-in-notebook")
        self.assertEqual(
            action.tables,
            ("samples", "inventory", "equipment", "collection-items"),
        )
        self.assertEqual((action.min_items, action.max_items), (1, 20))

    def test_login_url_carries_the_launch_code_only_in_the_fragment(self):
        settings = SimpleNamespace(
            browser_url=lambda _app_url: "https://jupyter.example.test/jupyter"
        )

        login_url = provider_module._login_url("launch code/value", settings)

        self.assertIn("#datalab_launch_code=launch+code%2Fvalue", login_url)
        self.assertNotIn("?datalab_launch_code=", login_url)

    def test_launch_requests_reuse_or_replace_with_renewal_permission(self):
        settings = SimpleNamespace(
            client_id="jupyter-client",
            browser_url=lambda _app_url: "https://jupyter.example.test/jupyter",
        )
        grants = create_autospec(ToolLaunchGrantIssuer, instance=True)
        grants.issue_service_launch.return_value = "launch-code"

        with (
            patch.object(provider_module.CONFIG.TOOLS, "ALLOW_INSECURE_HTTP", True),
            patch.object(
                provider_module, "load_plugin_settings", return_value=settings
            ) as settings_loader,
        ):
            provider = JupyterToolProvider()
        settings_loader.assert_called_once_with(allow_insecure_http=True)
        provider.launch(SimpleNamespace(), grants)

        grants.issue_service_launch.assert_called_once_with(
            "jupyter-client",
            "https://jupyter.example.test/jupyter/hub/datalab-callback",
            lifetime_seconds=5 * 60,
            session_policy=ToolSessionPolicy.REUSE_OR_REPLACE,
            allow_session_renewal=True,
        )

    def test_launch_allows_http_callback_only_for_insecure_testing(self):
        settings = SimpleNamespace(
            client_id="jupyter-client",
            browser_url=lambda _app_url: "http://jupyter.example.test/jupyter",
        )
        grants = create_autospec(ToolLaunchGrantIssuer, instance=True)
        grants.issue_service_launch.return_value = "launch-code"

        with patch.object(provider_module, "load_plugin_settings", return_value=settings):
            with patch.object(provider_module.CONFIG.TOOLS, "ALLOW_INSECURE_HTTP", False):
                provider = JupyterToolProvider()
                with self.assertRaisesRegex(ValueError, "must use HTTPS"):
                    provider.launch(SimpleNamespace(), grants)
            grants.issue_service_launch.assert_not_called()

            with patch.object(provider_module.CONFIG.TOOLS, "ALLOW_INSECURE_HTTP", True):
                provider = JupyterToolProvider()
                provider.launch(SimpleNamespace(), grants)

        grants.issue_service_launch.assert_called_once_with(
            "jupyter-client",
            "http://jupyter.example.test/jupyter/hub/datalab-callback",
            lifetime_seconds=5 * 60,
            session_policy=ToolSessionPolicy.REUSE_OR_REPLACE,
            allow_session_renewal=True,
        )

    def test_exchange_requires_service_credentials_and_uses_service_helper(self):
        settings = SimpleNamespace(
            client_id="jupyter-client",
            client_secret="a-long-confidential-service-secret",  # noqa: S106
        )
        exchange = SimpleNamespace(
            context=SimpleNamespace(
                user_id="0123456789abcdef01234567",
                display_name="Example User",
                role="user",
                group_ids=(),
            ),
            tool_session=SimpleNamespace(
                tool_access_token="temporary-token",  # noqa: S106
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            ),
            selection=None,
            session_reused=True,
            session_renewed=True,
        )

        with (
            patch.object(provider_module.CONFIG.TOOLS, "ALLOW_INSECURE_HTTP", True),
            patch.object(
                provider_module, "load_plugin_settings", return_value=settings
            ) as settings_loader,
            patch.object(
                provider_module,
                "exchange_authorized_service_launch_code",
                autospec=True,
                return_value=exchange,
            ) as exchange_mock,
        ):
            tool_provider = JupyterToolProvider()
            app = Flask(__name__)
            app.register_blueprint(JUPYTER_BLUEPRINT, url_prefix="/tool")

            @app.before_request
            def authenticate_service_request():
                if not tool_provider.authenticate_service_request():
                    return {"status": "error"}, 401
                return None

            client = app.test_client()
            response = client.post(
                "/tool/exchange",
                json={
                    "authorization_code": "authorization-code",
                    "code_verifier": "A" * 43,
                },
            )
            self.assertEqual(response.status_code, 401)

            invalid_credentials = b64encode(b"jupyter-client:wrong-secret").decode()
            response = client.post(
                "/tool/exchange",
                json={
                    "authorization_code": "authorization-code",
                    "code_verifier": "A" * 43,
                    "renew_existing_session": True,
                },
                headers={"Authorization": f"Basic {invalid_credentials}"},
            )
            self.assertEqual(response.status_code, 401)
            exchange_mock.assert_not_called()

            valid_credentials = b64encode(
                f"{settings.client_id}:{settings.client_secret}".encode()
            ).decode()
            response = client.post(
                "/tool/exchange",
                json={
                    "authorization_code": "authorization-code",
                    "code_verifier": "A" * 43,
                    "expected_user_id": "0123456789abcdef01234567",
                    "renew_existing_session": True,
                },
                headers={
                    "Authorization": f"Basic {valid_credentials}",
                    "DATALAB-EXISTING-TOOL-TOKEN": "existing-token",
                },
            )

            self.assertEqual(response.status_code, 200)
            exchange_mock.assert_called_once_with(
                "authorization-code",
                "A" * 43,
                JupyterToolProvider.id,
                settings.client_id,
                tool_session_lifetime_seconds=24 * 60 * 60,
                expected_user_id="0123456789abcdef01234567",
                candidate_tool_access_token="existing-token",  # noqa: S106
                minimum_reuse_lifetime_seconds=10 * 60,
                renew_existing_session=True,
            )
            self.assertTrue(response.get_json()["session_reused"])
            self.assertTrue(response.get_json()["session_renewed"])
            self.assertEqual(
                response.get_json()["expires_at"], exchange.tool_session.expires_at.isoformat()
            )
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(
                settings_loader.call_args_list,
                [
                    call(allow_insecure_http=True),
                    call(allow_insecure_http=True),
                ],
            )

    def test_exchange_rejects_malformed_or_unsupported_json(self):
        settings = SimpleNamespace(
            client_id="jupyter-client",
            client_secret="a-long-confidential-service-secret",  # noqa: S106
        )
        credentials = b64encode(f"{settings.client_id}:{settings.client_secret}".encode()).decode()
        with (
            patch.object(provider_module, "load_plugin_settings", return_value=settings),
            patch.object(
                provider_module, "exchange_authorized_service_launch_code", autospec=True
            ) as exchange_mock,
        ):
            provider = JupyterToolProvider()
            app = Flask(__name__)
            app.register_blueprint(JUPYTER_BLUEPRINT, url_prefix="/tool")

            @app.before_request
            def authenticate_service_request():
                if not provider.authenticate_service_request():
                    return {"status": "error"}, 401
                return None

            client = app.test_client()
            for payload in (
                None,
                [],
                {},
                {"code": "value", "token": "body-token"},
                {"authorization_code": "value"},
                {"authorization_code": "value", "code_verifier": "short"},
                {
                    "authorization_code": "value",
                    "code_verifier": "A" * 43,
                    "expected_user_id": "",
                },
                {
                    "authorization_code": "value",
                    "code_verifier": "A" * 43,
                    "expected_user_id": None,
                },
                {
                    "authorization_code": "value",
                    "code_verifier": "A" * 43,
                    "renew_existing_session": 1,
                },
                {
                    "authorization_code": "value",
                    "code_verifier": "A" * 43,
                    "renew_existing_session": "true",
                },
            ):
                with self.subTest(payload=payload):
                    response = client.post(
                        "/tool/exchange",
                        json=payload,
                        headers={"Authorization": f"Basic {credentials}"},
                    )
                    self.assertEqual(response.status_code, 400)
            exchange_mock.assert_not_called()
