"""tools/handoff_llm.py against a local fake Ollama / OpenAI-compatible server."""
import importlib.util
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

spec = importlib.util.spec_from_file_location("handoff_llm", Path(__file__).resolve().parents[1] / "tools" / "handoff_llm.py")
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)
ANSWER = {"headline": "local", "summary": "s", "market_view": {"stance": "neutral", "confidence": 50, "reasoning": "r"},
          "position_calls": [], "newsletter_picks": [], "new_ideas": [], "risks": [], "questions_for_user": [],
          "notes_for_next_run": ""}


class Fake(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Fake.seen.append((self.path, body, self.headers.get("Authorization")))
        text = json.dumps(ANSWER)
        out = ({"message": {"role": "assistant", "content": text}} if self.path == "/api/chat"
               else {"choices": [{"message": {"content": "```json\n" + text + "\n```"}}]})
        raw = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class HandoffLLMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Fake)
        cls.url = f"http://127.0.0.1:{cls.srv.server_port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        Fake.seen.clear()
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        (d / "prompt.md").write_text("You are the analyst")
        (d / "schema.json").write_text(json.dumps({"type": "object"}))
        (d / "facts.json").write_text('{"scope": "issue"}')
        (d / "news.txt").write_text("Ausgabe 22/2026")
        self.manifest = d / "manifest.json"
        self.out = d / "analysis.json"
        self.manifest.write_text(json.dumps({"prompt": str(d / "prompt.md"), "schema": str(d / "schema.json"),
                                             "tasks": [{"scope": "issue", "facts": str(d / "facts.json"),
                                                        "newsletter_text": str(d / "news.txt"), "output": str(self.out)}]}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_ollama_native_structured_output(self):
        H.main(["--manifest", str(self.manifest), "--api", "ollama", "--url", self.url, "--model", "qwen3:32b"])
        path, body, _ = Fake.seen[0]
        self.assertEqual(path, "/api/chat")
        self.assertEqual(body["format"], {"type": "object"})
        self.assertFalse(body["stream"])
        self.assertIn("Ausgabe 22/2026", body["messages"][1]["content"])
        self.assertEqual(json.loads(self.out.read_text())["headline"], "local")

    def test_openai_compatible_with_fenced_json_and_key(self):
        import os
        os.environ["LLM_API_KEY"] = "k"
        try:
            H.main(["--manifest", str(self.manifest), "--api", "openai", "--url", self.url, "--model", "m"])
        finally:
            del os.environ["LLM_API_KEY"]
        path, body, auth = Fake.seen[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(body["response_format"]["type"], "json_schema")
        self.assertEqual(auth, "Bearer k")
        self.assertEqual(json.loads(self.out.read_text())["headline"], "local")


if __name__ == "__main__":
    unittest.main()
