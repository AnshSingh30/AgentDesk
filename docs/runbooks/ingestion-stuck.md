# Runbook: ingestion stuck or failing

**Symptom**: documents stay `queued`/`parsing`/`embedding`, or fail with an error.

1. Check the worker is running and connected: `make worker` logs, `docker compose ps`, `arq` health key in Redis.
2. Look at `ingestion_jobs` (status, attempts, error) and `documents.error`. Common causes: unsupported/misdeclared MIME (magic bytes), scanned PDFs with no text (needs `ocr_only`/`hi_res` strategy and the worker-ingest image), embedding provider errors (key/rate limit), Qdrant down (`/readyz`).
3. Retry from the KB page (Retry) or `POST /documents/{id}/retry`. A re-upload with the same filename creates a new version and retires the old one.
4. If Qdrant lost data, run "Re-index all documents" on the KB.
