import os
from unittest import TestCase
from unittest.mock import patch

from datalab_jupyter.config import load_plugin_settings, validate_http_base_url


class PluginSettingsTests(TestCase):
    def test_pure_http_base_validation_requires_tls_except_loopback(self):
        self.assertEqual(
            validate_http_base_url(
                "https://api.example.test/prefix/",
                name="TEST_URL",
            ),
            "https://api.example.test/prefix",
        )
        self.assertEqual(
            validate_http_base_url("http://localhost:5001", name="TEST_URL"),
            "http://localhost:5001",
        )
        for value in (
            "",
            "api.example.test",
            "http://api.example.test",
            "https://user:secret@api.example.test",
            "https://api.example.test/path?query=value",
            "https://api.example.test/path#fragment",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_http_base_url(value, name="TEST_URL")

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
