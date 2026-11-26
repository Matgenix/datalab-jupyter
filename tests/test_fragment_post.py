import re
from types import SimpleNamespace
from unittest import TestCase

from datalab_jupyter.fragment_post import render_fragment_post, render_renewal_confirmation


class _Handler:
    xsrf_token = b"test-xsrf-token"

    def __init__(self):
        self.request = SimpleNamespace(path="/hub/datalab-login")
        self.headers = {}

    def set_header(self, name, value):
        self.headers[name] = value


class FragmentPostTests(TestCase):
    def test_bootstrap_posts_one_fragment_field_with_xsrf_and_strict_headers(self):
        handler = _Handler()

        page = render_fragment_post(handler, "datalab_launch_code")

        self.assertIn('method="post" action="/hub/datalab-login"', page)
        self.assertIn('name="_xsrf" value="test-xsrf-token"', page)
        self.assertIn('name="datalab_launch_code" value=""', page)
        self.assertIn('const expectedField = "datalab_launch_code";', page)
        self.assertIn("entries.length !== 1", page)
        self.assertIn("entries[0][0] !== expectedField", page)
        self.assertIn("decodeURIComponent", page)
        self.assertIn("history.replaceState", page)
        self.assertLess(page.index("history.replaceState"), page.index("new URLSearchParams"))
        self.assertNotIn("test-launch-credential", page)
        self.assertEqual(handler.headers["Cache-Control"], "no-store")
        self.assertEqual(handler.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(handler.headers["X-Content-Type-Options"], "nosniff")

        nonce_match = re.search(r'<script nonce="([^"]+)">', page)
        self.assertIsNotNone(nonce_match)
        self.assertIn(
            f"script-src 'nonce-{nonce_match.group(1)}'",
            handler.headers["Content-Security-Policy"],
        )
        self.assertIn("form-action 'self'", handler.headers["Content-Security-Policy"])

    def test_bootstrap_rejects_an_invalid_field_name(self):
        with self.assertRaises(ValueError):
            render_fragment_post(_Handler(), "unexpected-field")

    def test_confirmation_escapes_bound_values_and_uses_protected_post(self):
        handler = _Handler()
        handler.request.path = '/hub/datalab-login"><script>'

        page = render_renewal_confirmation(
            handler,
            'launch"><secret',
            'signed"><binding',
        )

        self.assertIn('method="post" action="/hub/datalab-login&quot;&gt;&lt;script&gt;"', page)
        self.assertIn('name="_xsrf" value="test-xsrf-token"', page)
        self.assertIn('value="launch&quot;&gt;&lt;secret"', page)
        self.assertIn('value="signed&quot;&gt;&lt;binding"', page)
        self.assertIn('name="datalab_authorization_code"', page)
        self.assertNotIn('name="datalab_launch_code"', page)
        self.assertIn('name="renew_existing_session" value="false"', page)
        self.assertIn('name="renew_existing_session" value="true"', page)
        self.assertNotIn("<secret", page)
        self.assertEqual(handler.headers["Cache-Control"], "no-store")
        self.assertEqual(handler.headers["Referrer-Policy"], "no-referrer")
        self.assertIn("default-src 'none'", handler.headers["Content-Security-Policy"])
        self.assertIn("form-action 'self'", handler.headers["Content-Security-Policy"])
        self.assertIn("base-uri 'none'", handler.headers["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", handler.headers["Content-Security-Policy"])
