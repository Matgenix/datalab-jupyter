import os
from unittest import TestCase
from unittest.mock import patch

from datalab_jupyter.urls import configured_base_url, workspace_url


class UrlTests(TestCase):
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

    def test_workspace_url_uses_server_prefix_and_quotes_components(self):
        self.assertEqual(
            workspace_url(
                "/nested/jupyter/user/name%40example.test/",
                workspace_id="workspace / one",
            ),
            "/nested/jupyter/user/name%40example.test/lab/workspaces/workspace%20%2F%20one",
        )
        self.assertEqual(
            workspace_url(
                "/",
                workspace_id="workspace",
                notebook_path="folder/Selected items #1.ipynb",
            ),
            "/lab/workspaces/workspace/tree/folder/Selected%20items%20%231.ipynb",
        )

    def test_workspace_ids_are_fresh_and_destinations_contain_no_credentials(self):
        first = workspace_url("/user/test/")
        second = workspace_url("/user/test/")

        self.assertNotEqual(first, second)
        self.assertNotIn("token", first)
        self.assertNotIn("launch-code", first)
        self.assertNotIn("refcode", first)
