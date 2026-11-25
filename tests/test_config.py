import os
from unittest import TestCase
from unittest.mock import patch

from datalab_jupyter.config import load_plugin_settings


class PluginSettingsTests(TestCase):
    def test_public_and_external_http_urls_follow_core_policy(self):
        for environment_name, attribute in (
            ("DATALAB_JUPYTER_PUBLIC_URL", "public_url"),
            ("DATALAB_JUPYTER_EXTERNAL_URL", "external_url"),
        ):
            environment = {
                "DATALAB_JUPYTER_CLIENT_SECRET": "a" * 32,
                environment_name: "http://192.0.2.10/jupyter",
            }
            with self.subTest(environment_name=environment_name):
                with patch.dict(os.environ, environment, clear=True):
                    with self.assertRaisesRegex(ValueError, "must use HTTPS"):
                        load_plugin_settings(allow_insecure_http=False)

                    settings = load_plugin_settings(allow_insecure_http=True)

                self.assertEqual(
                    getattr(settings, attribute),
                    "http://192.0.2.10/jupyter",
                )
