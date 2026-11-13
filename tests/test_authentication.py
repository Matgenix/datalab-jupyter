from datetime import datetime, timedelta, timezone
from unittest import TestCase

from datalab_jupyter.hub.authenticator import _ExchangeResponse, _is_expired


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
        self.assertFalse(_is_expired(response.expires_at))

    def test_exchange_response_rejects_invalid_fields(self):
        invalid_values = [
            ("user_id", ""),
            ("group_ids", [1]),
            ("tool_access_token", ""),
            ("expires_at", "not-a-date"),
            ("notebook_launch_code", ""),
        ]
        for field, value in invalid_values:
            with self.subTest(field=field):
                payload = valid_payload()
                payload[field] = value
                with self.assertRaises(ValueError):
                    _ExchangeResponse.from_payload(payload)
