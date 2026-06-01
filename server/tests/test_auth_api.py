import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import flask_app


class FragAuthApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        flask_app.USER_DATA_ROOT = Path(self._tmp.name)
        self.client = flask_app.app.test_client()
        self.headers = {"User-Agent": "FragModdingClient/v1"}

    def tearDown(self):
        self._tmp.cleanup()

    def test_authenticate_requires_token(self):
        resp = self.client.get("/frag/v1/authenticate", headers=self.headers)
        self.assertEqual(resp.status_code, 400)

    def test_authenticate_accepts_valid_token(self):
        with (
            patch("flask_app.validate_token", return_value=True),
            patch("flask_app.decode_token", return_value={"user_id": "user123"}),
        ):
            resp = self.client.get(
                "/frag/v1/authenticate?token=test-token", headers=self.headers
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["user_id"], "user123")
        self.assertEqual(data["token_type"], "Bearer")

    def test_files_requires_bearer_token(self):
        resp = self.client.get("/frag/v1/files/", headers=self.headers)
        self.assertEqual(resp.status_code, 401)

    def test_files_list_works_for_valid_bearer(self):
        with patch("flask_app.decode_token", return_value={"user_id": "user123"}):
            resp = self.client.get(
                "/frag/v1/files/",
                headers={
                    **self.headers,
                    "Authorization": "Bearer valid-token",
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["files"], [])

    def test_invalid_subject_is_rejected(self):
        with patch("flask_app.decode_token", return_value={"user_id": "../bad"}):
            resp = self.client.get(
                "/frag/v1/files/",
                headers={
                    **self.headers,
                    "Authorization": "Bearer valid-token",
                },
            )
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
