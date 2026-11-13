import os
from unittest import TestCase
from unittest.mock import patch

from datalab_jupyter.urls import configured_base_url, redact_query_parameter


class UrlTests(TestCase):
    def test_redact_query_parameter_preserves_other_values(self):
        self.assertEqual(
            redact_query_parameter(
                "/login?next=%2Fhub&datalab_launch_code=secret&empty=",
                "datalab_launch_code",
            ),
            "/login?next=%2Fhub&empty=",
        )

    def test_configured_base_url(self):
        with patch.dict(
            os.environ,
            {"PYDATALAB_APP_URL": "https://example.test/lab"},
            clear=True,
        ):
            self.assertEqual(configured_base_url(), "/lab/jupyter/")

        with patch.dict(
            os.environ,
            {"DATALAB_JUPYTER_PUBLIC_URL": "https://jupyter.example.test/hub/"},
            clear=True,
        ):
            self.assertEqual(configured_base_url(), "/hub/")
