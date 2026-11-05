# paperless-api

Small stdlib-only client for the [paperless-ngx](https://docs.paperless-ngx.com/api/) REST API.
Python 3.8+, no dependencies.

## Setup

```bash
export PAPERLESS_URL=http://paperless.lan:8000
export PAPERLESS_TOKEN=...   # paperless web UI: My Profile -> API auth token
```

## CLI

```bash
python paperless_api.py ping                          # check URL and token
python paperless_api.py docs --query "invoice" --limit 20
python paperless_api.py doc 42
python paperless_api.py download 42 ./42.pdf          # archived PDF; add --original for source file
python paperless_api.py upload scan.pdf --title "Scan" --created 2024-05-01 --tag 3 --correspondent 9
python paperless_api.py task <task-uuid>              # consume status after upload
python paperless_api.py tags | correspondents | doctypes
```

## Library

```python
from paperless_api import PaperlessClient

c = PaperlessClient("http://paperless.lan:8000", token="...")
for doc in c.iter_documents(query="invoice"):
    print(doc["id"], doc["title"])
c.update_document(42, tags=[1, 2])
task_id = c.upload_document("scan.pdf", title="Scan", tags=[3])
```

Uploads are asynchronous: `upload_document` returns a task UUID, and the document id appears once
paperless has finished consuming the file (poll with `get_task`).

## Tests

No server needed; the tests run against a local fake of the API.

```bash
python -m unittest -v test_paperless_api
```

## Notes

- Not yet exercised against a real paperless-ngx instance. Check `ping` first.
- Endpoints used: `documents/`, `documents/<id>/`, `documents/<id>/download/`,
  `documents/post_document/`, `tags/`, `correspondents/`, `document_types/`, `tasks/`.
- Relation to `../paperless-ingest-prep`: that script stages files into the consume folder;
  this client talks to the running instance.
