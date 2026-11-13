import importlib.util
from unittest import TestCase, skipUnless

TOOLS_API_AVAILABLE = importlib.util.find_spec("pydatalab.tools") is not None

if TOOLS_API_AVAILABLE:
    from datalab_jupyter.plugin.provider import JupyterToolProvider


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
