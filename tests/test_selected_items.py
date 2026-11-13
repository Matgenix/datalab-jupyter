from unittest import TestCase

from datalab_jupyter.server.selected_items import _initialization_source, _selection_banner


class SelectedItemNotebookTests(TestCase):
    def test_content_is_visible_and_rerunnable(self):
        source = _initialization_source(("lab:one", "lab:two"))
        banner = _selection_banner(2)

        self.assertIn("selected_item_refcodes", source)
        self.assertIn('"lab:one"', source)
        self.assertIn("selected_item_errors", source)
        self.assertIn("2 items", banner)
