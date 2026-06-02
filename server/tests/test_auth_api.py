"""Smoke tests for the frag server auth flow.

Mocks the PDClient so PD is not actually contacted.
"""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("FRAG_PD_API_KEY", "pd_test_dummy")


class FragAuthApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        import flask_app
        flask_app.USER_DATA_ROOT = Path(self._tmp.name)
        flask_app.sessions = flask_app.SessionCache(
            Path(self._tmp.name) / ".sessions.json"
        )
        self.flask_app = flask_app
        self.client = flask_app.app.test_client()

    def tearDown(self):
        self._tmp.cleanup()

    # ---- public endpoints ------------------------------------------------

    def test_ping(self):
        resp = self.client.post("/frag/v1/ping")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["message"], "pong")

    def test_healthz(self):
        resp = self.client.get("/healthz")
        self.assertEqual(resp.status_code, 200)

    # ---- auth/start ------------------------------------------------------

    def test_auth_start_returns_url_and_handle(self):
        with patch.object(self.flask_app.pd, "start_login", return_value={
            "handle": "h-123",
            "authorize_url": "https://pixelateddream.net/auth/broker/h-123",
            "expires_in": 600,
        }):
            resp = self.client.post("/frag/v1/auth/start")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["handle"], "h-123")
        self.assertIn("authorize_url", body)

    def test_auth_start_propagates_pd_error(self):
        from flask_app import PDClientError
        with patch.object(self.flask_app.pd, "start_login", side_effect=PDClientError("boom")):
            resp = self.client.post("/frag/v1/auth/start")
        self.assertEqual(resp.status_code, 502)

    # ---- auth/poll -------------------------------------------------------

    def test_auth_poll_pending(self):
        with patch.object(self.flask_app.pd, "poll_login", return_value={"status": "pending"}):
            resp = self.client.get("/frag/v1/auth/poll?handle=h-1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "pending")

    def test_auth_poll_approved_caches_session(self):
        future = "2999-01-01T00:00:00+00:00"
        with patch.object(self.flask_app.pd, "poll_login", return_value={
            "status": "approved",
            "session_token": "pdb_abc",
            "user_id": "uuid-1",
            "expires_at": future,
        }):
            resp = self.client.get("/frag/v1/auth/poll?handle=h-1")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["status"], "approved")
        self.assertEqual(body["session_token"], "pdb_abc")
        cached = self.flask_app.sessions.get("pdb_abc")
        self.assertIsNotNone(cached)
        self.assertEqual(cached.user_id, "uuid-1")

    def test_auth_poll_requires_handle(self):
        resp = self.client.get("/frag/v1/auth/poll")
        self.assertEqual(resp.status_code, 400)

    # ---- login_required gate --------------------------------------------

    def test_protected_endpoint_rejects_without_token(self):
        resp = self.client.get("/frag/v1/files/")
        self.assertEqual(resp.status_code, 401)

    def test_protected_endpoint_accepts_cached_session(self):
        # Seed the cache with a still-fresh, PD-valid entry
        from session_cache import CachedSession
        self.flask_app.sessions.put(CachedSession(
            token="pdb_xyz",
            user_id="uuid-2",
            username="alice",
            expires_at=time.time() + 3600,
            cached_at=time.time(),
        ))
        resp = self.client.get(
            "/frag/v1/files/",
            headers={"Authorization": "Bearer pdb_xyz"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["user_id"], "uuid-2")

    def test_protected_endpoint_revalidates_stale_cache(self):
        from session_cache import CachedSession
        # Cache entry past revalidation window
        self.flask_app.sessions.put(CachedSession(
            token="pdb_stale",
            user_id="uuid-3",
            username="bob",
            expires_at=time.time() + 3600,
            cached_at=time.time() - 9999,
        ))
        with patch.object(self.flask_app.pd, "validate_session", return_value={
            "valid": True,
            "user_id": "uuid-3",
            "username": "bob",
            "expires_at": "2999-01-01T00:00:00+00:00",
        }) as mock_validate:
            resp = self.client.get(
                "/frag/v1/files/",
                headers={"Authorization": "Bearer pdb_stale"},
            )
        self.assertEqual(resp.status_code, 200)
        mock_validate.assert_called_once()

    def test_protected_endpoint_rejects_revoked_session(self):
        from session_cache import CachedSession
        self.flask_app.sessions.put(CachedSession(
            token="pdb_revoked",
            user_id="uuid-4",
            username="charlie",
            expires_at=time.time() + 3600,
            cached_at=time.time() - 9999,  # forces revalidation
        ))
        with patch.object(self.flask_app.pd, "validate_session", return_value={
            "valid": False,
            "reason": "revoked",
        }):
            resp = self.client.get(
                "/frag/v1/files/",
                headers={"Authorization": "Bearer pdb_revoked"},
            )
        self.assertEqual(resp.status_code, 401)
        # Cache entry was dropped
        self.assertIsNone(self.flask_app.sessions.get("pdb_revoked"))


if __name__ == "__main__":
    unittest.main()
