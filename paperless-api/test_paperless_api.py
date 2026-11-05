"""Tests for paperless_api.py against a local fake paperless-ngx server (no network needed).

Run:  python -m unittest -v test_paperless_api
"""
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from paperless_api import PaperlessClient, PaperlessError

TOKEN = "test-token"


class FakePaperless(BaseHTTPRequestHandler):
    seen: list = []  # (method, path, query, headers, body)

    def log_message(self, *args):  # keep test output quiet
        pass

    def _send(self, code, payload):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _record(self, body=b""):
        u = urlparse(self.path)
        FakePaperless.seen.append((self.command, u.path, parse_qs(u.query), dict(self.headers), body))

    def _auth_ok(self):
        return self.headers.get("Authorization") == f"Token {TOKEN}"

    def do_GET(self):
        self._record()
        if not self._auth_ok():
            return self._send(401, {"detail": "Invalid token."})
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/api/documents/":
            page = int(q.get("page", ["1"])[0])
            if page == 1:
                return self._send(200, {"count": 3, "next": "x", "results": [{"id": 1, "title": "A"}, {"id": 2, "title": "B"}]})
            return self._send(200, {"count": 3, "next": None, "results": [{"id": 3, "title": "C"}]})
        if u.path == "/api/documents/7/":
            return self._send(200, {"id": 7, "title": "Seven"})
        if u.path == "/api/documents/7/download/":
            return self._send(200, b"%PDF-fake")
        if u.path == "/api/tags/":
            return self._send(200, {"count": 1, "next": None, "results": [{"id": 3, "name": "taxes"}]})
        if u.path == "/api/tasks/":
            return self._send(200, [{"task_id": q["task_id"][0], "status": "SUCCESS"}])
        if u.path == "/api/":
            return self._send(200, {"documents": "http://x/api/documents/"})
        self._send(404, {"detail": "Not found."})

    def do_PATCH(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self._record(body)
        self._send(200, {"id": 7, **json.loads(body or b"{}")})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self._record(body)
        if self.path == "/api/documents/post_document/":
            return self._send(200, b'"0f3c-task-uuid"')
        self._send(404, {"detail": "Not found."})

    def do_DELETE(self):
        self._record()
        self._send(204, b"")


class PaperlessClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakePaperless)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakePaperless.seen.clear()
        self.c = PaperlessClient(self.url, TOKEN)

    def test_ping_sends_token_and_hits_api_root(self):
        self.assertIn("documents", self.c.ping())
        method, path, _, headers, _ = FakePaperless.seen[-1]
        self.assertEqual((method, path), ("GET", "/api/"))
        self.assertEqual(headers["Authorization"], f"Token {TOKEN}")

    def test_bad_token_raises_paperless_error(self):
        with self.assertRaisesRegex(PaperlessError, "HTTP 401"):
            PaperlessClient(self.url, "wrong").ping()

    def test_accepts_url_with_api_suffix(self):
        self.assertEqual(PaperlessClient(self.url + "/api/", TOKEN).base_url, self.url)

    def test_iter_documents_follows_pagination(self):
        titles = [d["title"] for d in self.c.iter_documents(page_size=2)]
        self.assertEqual(titles, ["A", "B", "C"])

    def test_iter_documents_passes_query(self):
        list(self.c.iter_documents(query="invoice"))
        _, path, query, _, _ = FakePaperless.seen[0]
        self.assertEqual(path, "/api/documents/")
        self.assertEqual(query["query"], ["invoice"])

    def test_get_and_update_document(self):
        self.assertEqual(self.c.get_document(7)["title"], "Seven")
        self.c.update_document(7, title="New", tags=[1, 2])
        method, path, _, headers, body = FakePaperless.seen[-1]
        self.assertEqual((method, path), ("PATCH", "/api/documents/7/"))
        self.assertEqual(json.loads(body), {"title": "New", "tags": [1, 2]})
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_delete_document(self):
        self.c.delete_document(7)
        self.assertEqual(FakePaperless.seen[-1][:2], ("DELETE", "/api/documents/7/"))

    def test_download_document_writes_file(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "out.pdf"
            self.c.download_document(7, dest)
            self.assertEqual(dest.read_bytes(), b"%PDF-fake")

    def test_upload_builds_multipart_body(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "scan.pdf"
            f.write_bytes(b"%PDF-1.4 hello")
            task = self.c.upload_document(f, title="Scan", created="2024-05-01", tags=[3, 4], correspondent=9)
        self.assertEqual(task, "0f3c-task-uuid")
        method, path, _, headers, body = FakePaperless.seen[-1]
        self.assertEqual((method, path), ("POST", "/api/documents/post_document/"))
        self.assertTrue(headers["Content-Type"].startswith("multipart/form-data; boundary="))
        text = body.decode("latin-1")
        self.assertIn('name="title"\r\n\r\nScan', text)
        self.assertIn('name="created"\r\n\r\n2024-05-01', text)
        self.assertIn('name="correspondent"\r\n\r\n9', text)
        self.assertEqual(text.count('name="tags"'), 2)
        self.assertIn('name="document"; filename="scan.pdf"', text)
        self.assertIn("Content-Type: application/pdf", text)
        self.assertIn("%PDF-1.4 hello", text)

    def test_lookups_and_task(self):
        self.assertEqual(self.c.list_tags(), [{"id": 3, "name": "taxes"}])
        self.assertEqual(self.c.get_task("abc")[0]["status"], "SUCCESS")
        self.assertEqual(FakePaperless.seen[-1][2]["task_id"], ["abc"])

    def test_unreachable_server_raises_paperless_error(self):
        with self.assertRaises(PaperlessError):
            PaperlessClient("http://127.0.0.1:1", TOKEN, timeout=2).ping()

    def test_requires_url_and_token(self):
        with self.assertRaises(ValueError):
            PaperlessClient("", TOKEN)
        with self.assertRaises(ValueError):
            PaperlessClient(self.url, "")


if __name__ == "__main__":
    unittest.main()
