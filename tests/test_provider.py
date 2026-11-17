import importlib.util
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import TestCase, skipUnless
from unittest.mock import patch

TOOLS_API_AVAILABLE = (
    importlib.util.find_spec("pydatalab") is not None
    and importlib.util.find_spec("pydatalab.tools") is not None
)

if TOOLS_API_AVAILABLE:
    from flask import Flask

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
        )

        with (
            patch.object(provider_module, "load_plugin_settings", return_value=settings),
            patch.object(
                provider_module,
                "exchange_service_launch_code",
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
            response = client.post("/tool/exchange", json={"code": "launch-code"})
            self.assertEqual(response.status_code, 401)

            invalid_credentials = b64encode(b"jupyter-client:wrong-secret").decode()
            response = client.post(
                "/tool/exchange",
                json={"code": "launch-code"},
                headers={"Authorization": f"Basic {invalid_credentials}"},
            )
            self.assertEqual(response.status_code, 401)
            exchange_mock.assert_not_called()

            valid_credentials = b64encode(
                f"{settings.client_id}:{settings.client_secret}".encode()
            ).decode()
            response = client.post(
                "/tool/exchange",
                json={"code": "launch-code"},
                headers={"Authorization": f"Basic {valid_credentials}"},
            )

            self.assertEqual(response.status_code, 200)
            exchange_mock.assert_called_once_with(
                "launch-code",
                JupyterToolProvider.id,
                settings.client_id,
            )
