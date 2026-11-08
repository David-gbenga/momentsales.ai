# ADR-0012: File storage behind a port

- Status: Accepted (Phase 1)
- Date: 2026-11-07

## Context
Receipts (FR-7) and audit packs (FR-8) need private object storage with short-lived signed URLs. The app must run locally with no cloud account.

## Decision
- `StoragePort` with `put(key, bytes, content_type)`, `get(key)`, `signed_url(key, ttl, filename)`. Two adapters:
  - **`local`**: files in a Docker volume (`STORAGE_LOCAL_PATH`). Download URLs are `/api/v1/files/{token}`, where the token is an HMAC (`SIGNING_SECRET`) over key, workspace and expiry. The route checks the signature and expiry. Like an S3 presigned URL, the short-lived signed link is the permission (a browser download can't carry the bearer token); links are only issued to members with the right role.
  - **`s3`**: boto3, private bucket, server-side encryption, presigned GET URLs. Works with Cloudflare R2 through `S3_ENDPOINT_URL`. Tested with moto. Storage has its own credentials and region (`S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`; region `auto` for R2), separate from the `AWS_*` settings Textract uses, so storage can be on R2 while OCR is on AWS. If the keys are empty, boto3's default credential chain applies.
- Keys are `{workspace_id}/{kind}/{uuid}`; the workspace prefix is checked on every read.
- Originals are stored unaltered, with SHA-256 recorded in the database before the row commits. Objects are never overwritten.
- Signed URLs live 5 minutes (`SIGNED_URL_TTL_SECONDS=300`).

## Consequences
- No MinIO (AGPL) container is needed. The local adapter is for development only; settings refuse `STORAGE_BACKEND=local` outside `local`/`test`.
