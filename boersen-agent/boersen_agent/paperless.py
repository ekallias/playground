#!/usr/bin/env python3
"""Vendored copy of ../paperless-api/paperless_api.py (keep in sync).

paperless_api.py - minimal client for the paperless-ngx REST API (stdlib only).

Configure with environment variables (or pass --url / --token):

    export PAPERLESS_URL=http://paperless.lan:8000
    export PAPERLESS_TOKEN=...          # Settings -> My Profile -> API auth token

Library use:

    from paperless_api import PaperlessClient
    c = PaperlessClient("http://paperless.lan:8000", token="...")
    for doc in c.iter_documents(query="invoice 2024"):
        print(doc["id"], doc["title"])
    c.upload_document("scan.pdf", title="Scan", tags=[3])

CLI use:

    python paperless_api.py ping
    python paperless_api.py docs --query "invoice" --limit 20
    python paperless_api.py doc 42
    python paperless_api.py upload scan.pdf --title "Scan" --tag 3
    python paperless_api.py task <task-uuid>
    python paperless_api.py tags | correspondents | doctypes
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Iterator
from urllib import error, parse, request


class PaperlessError(RuntimeError):
    """Raised for HTTP errors or unexpected responses from paperless-ngx."""


class PaperlessClient:
    def __init__(self, base_url: str, token: str, timeout: float = 30.0):
        if not base_url:
            raise ValueError("base_url is required")
        if not token:
            raise ValueError("token is required")
        base = base_url.rstrip("/")
        # Accept either the server root or a URL that already ends in /api
        self.base_url = base[:-4] if base.endswith("/api") else base
        self.token = token
        self.timeout = timeout

    # ------------------------------------------------------------------ core
    def _url(self, path: str, params: dict[str, Any] | None = None) -> str:
        url = f"{self.base_url}/api/{path.lstrip('/')}"
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += "?" + parse.urlencode(clean, doseq=True)
        return url

    def _request(self, method: str, path: str, params=None, body: bytes | None = None,
                 content_type: str | None = None) -> Any:
        headers = {"Authorization": f"Token {self.token}", "Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        if body is None and method in ("POST", "PATCH", "PUT"):
            body = b"{}" if content_type is None else body
        req = request.Request(self._url(path, params), data=body, method=method, headers=headers)
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            raise PaperlessError(f"{method} {path} -> HTTP {e.code}: {detail}") from None
        except error.URLError as e:
            raise PaperlessError(f"{method} {path} failed: {e.reason}") from None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # e.g. post_document returns a bare task id string
            return raw.decode("utf-8", "replace").strip().strip('"')

    def _get(self, path: str, **params) -> Any:
        return self._request("GET", path, params=params)

    def _json(self, method: str, path: str, data: dict[str, Any]) -> Any:
        return self._request(method, path, body=json.dumps(data).encode("utf-8"),
                             content_type="application/json")

    # ------------------------------------------------------------ connectivity
    def ping(self) -> dict[str, Any]:
        """Check the connection and token with a cheap authenticated call.
        Raises PaperlessError if the server is unreachable or the token is rejected."""
        return self._get("documents/", page_size=1)

    # --------------------------------------------------------------- documents
    def list_documents(self, page: int = 1, page_size: int = 25, **filters) -> dict[str, Any]:
        """One page of documents. Filters are passed through, e.g. query=, correspondent__id=,
        tags__id__all=, created__date__gt=, ordering=-created."""
        return self._get("documents/", page=page, page_size=page_size, **filters)

    def iter_documents(self, page_size: int = 100, **filters) -> Iterator[dict[str, Any]]:
        """All documents matching the filters, following pagination."""
        page = 1
        while True:
            data = self.list_documents(page=page, page_size=page_size, **filters)
            yield from data.get("results", [])
            if not data.get("next"):
                break
            page += 1

    def get_document(self, doc_id: int) -> dict[str, Any]:
        return self._get(f"documents/{int(doc_id)}/")

    def update_document(self, doc_id: int, **fields) -> dict[str, Any]:
        """PATCH only the given fields, e.g. title=..., tags=[1, 2], correspondent=5."""
        return self._json("PATCH", f"documents/{int(doc_id)}/", fields)

    def delete_document(self, doc_id: int) -> None:
        self._request("DELETE", f"documents/{int(doc_id)}/")

    def download_document(self, doc_id: int, dest: str | Path, original: bool = False) -> Path:
        """Save the archived PDF (or the original file when original=True) to dest."""
        params = {"original": "true"} if original else None
        req = request.Request(self._url(f"documents/{int(doc_id)}/download/", params),
                              headers={"Authorization": f"Token {self.token}"})
        try:
            with request.urlopen(req, timeout=self.timeout) as resp, open(dest, "wb") as out:
                while chunk := resp.read(1 << 20):
                    out.write(chunk)
        except error.HTTPError as e:
            raise PaperlessError(f"download {doc_id} -> HTTP {e.code}") from None
        return Path(dest)

    def upload_document(self, path: str | Path, title: str | None = None,
                        created: str | None = None, correspondent: int | None = None,
                        document_type: int | None = None, storage_path: int | None = None,
                        tags: list[int] | None = None, archive_serial_number: int | None = None) -> str:
        """Upload a file to the consume queue. Returns the paperless task UUID.
        Poll it with get_task(); the document id appears once processing finishes."""
        path = Path(path)
        fields: list[tuple[str, str]] = []
        if title:
            fields.append(("title", title))
        if created:
            fields.append(("created", created))  # YYYY-MM-DD
        if correspondent is not None:
            fields.append(("correspondent", str(correspondent)))
        if document_type is not None:
            fields.append(("document_type", str(document_type)))
        if storage_path is not None:
            fields.append(("storage_path", str(storage_path)))
        if archive_serial_number is not None:
            fields.append(("archive_serial_number", str(archive_serial_number)))
        for t in tags or []:
            fields.append(("tags", str(t)))

        boundary = uuid.uuid4().hex
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        parts: list[bytes] = []
        for name, value in fields:
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                         f"{value}\r\n".encode("utf-8"))
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; "
                     f"filename=\"{path.name}\"\r\nContent-Type: {mime}\r\n\r\n".encode("utf-8"))
        parts.append(path.read_bytes())
        parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(parts)

        result = self._request("POST", "documents/post_document/", body=body,
                               content_type=f"multipart/form-data; boundary={boundary}")
        if not isinstance(result, str):
            raise PaperlessError(f"unexpected upload response: {result!r}")
        return result

    # -------------------------------------------------------------- lookups
    def list_tags(self, **filters) -> list[dict[str, Any]]:
        return list(self._paged("tags/", **filters))

    def list_correspondents(self, **filters) -> list[dict[str, Any]]:
        return list(self._paged("correspondents/", **filters))

    def list_document_types(self, **filters) -> list[dict[str, Any]]:
        return list(self._paged("document_types/", **filters))

    def get_task(self, task_id: str) -> Any:
        """Status of a consume task. Returns a list (paperless <2.x style) or dict."""
        return self._get("tasks/", task_id=task_id)

    def _paged(self, path: str, **filters) -> Iterator[dict[str, Any]]:
        page = 1
        while True:
            data = self._get(path, page=page, page_size=100, **filters)
            if isinstance(data, list):  # some endpoints return a bare list
                yield from data
                return
            yield from data.get("results", [])
            if not data.get("next"):
                return
            page += 1


# ------------------------------------------------------------------ CLI
def _client(args) -> PaperlessClient:
    url = args.url or os.environ.get("PAPERLESS_URL")
    token = args.token or os.environ.get("PAPERLESS_TOKEN")
    if not url or not token:
        sys.exit("set PAPERLESS_URL and PAPERLESS_TOKEN (or pass --url and --token)")
    return PaperlessClient(url, token)


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="paperless-ngx REST API client")
    ap.add_argument("--url", help="paperless base URL (default: $PAPERLESS_URL)")
    ap.add_argument("--token", help="API token (default: $PAPERLESS_TOKEN)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ping", help="check connection and token")

    p = sub.add_parser("docs", help="list or search documents")
    p.add_argument("--query", help="full-text search query")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--ordering", default="-created")

    p = sub.add_parser("doc", help="show one document")
    p.add_argument("id", type=int)

    p = sub.add_parser("download", help="download a document file")
    p.add_argument("id", type=int)
    p.add_argument("dest")
    p.add_argument("--original", action="store_true")

    p = sub.add_parser("upload", help="upload a file to the consume queue")
    p.add_argument("file")
    p.add_argument("--title")
    p.add_argument("--created", help="YYYY-MM-DD")
    p.add_argument("--correspondent", type=int)
    p.add_argument("--doctype", type=int, dest="document_type")
    p.add_argument("--tag", type=int, action="append", default=[], dest="tags")

    p = sub.add_parser("task", help="status of an upload task")
    p.add_argument("task_id")

    sub.add_parser("tags", help="list tags")
    sub.add_parser("correspondents", help="list correspondents")
    sub.add_parser("doctypes", help="list document types")

    args = ap.parse_args(argv)
    try:
        c = _client(args)
        if args.cmd == "ping":
            _print(c.ping())
        elif args.cmd == "docs":
            filters = {"query": args.query} if args.query else {"ordering": args.ordering}
            docs = []
            for doc in c.iter_documents(**filters):
                docs.append({"id": doc["id"], "title": doc["title"], "created": doc.get("created")})
                if len(docs) >= args.limit:
                    break
            _print(docs)
        elif args.cmd == "doc":
            _print(c.get_document(args.id))
        elif args.cmd == "download":
            print(c.download_document(args.id, args.dest, original=args.original))
        elif args.cmd == "upload":
            print(c.upload_document(args.file, title=args.title, created=args.created,
                                    correspondent=args.correspondent,
                                    document_type=args.document_type, tags=args.tags))
        elif args.cmd == "task":
            _print(c.get_task(args.task_id))
        elif args.cmd == "tags":
            _print([{"id": t["id"], "name": t["name"]} for t in c.list_tags()])
        elif args.cmd == "correspondents":
            _print([{"id": t["id"], "name": t["name"]} for t in c.list_correspondents()])
        elif args.cmd == "doctypes":
            _print([{"id": t["id"], "name": t["name"]} for t in c.list_document_types()])
    except PaperlessError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
