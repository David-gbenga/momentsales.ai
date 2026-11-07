"""S3 / Cloudflare R2 storage (boto3). Private bucket, presigned GET URLs (ADR-0012).

boto3 is synchronous, so calls run in a worker thread. No encryption header is sent: S3
encrypts every new object (SSE-S3) by default and R2 encrypts at rest automatically, while
R2 does not implement `x-amz-server-side-encryption` (docs/integrations/s3-r2.md).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

from botocore.exceptions import BotoCoreError, ClientError

from momentsales.adapters.storage.base import StorageError

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client


class S3StorageAdapter:
    name = "s3"

    def __init__(self, client: S3Client, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    async def put(self, key: str, content: bytes, content_type: str) -> None:
        def _put() -> None:
            try:
                # IfNoneMatch="*" makes S3 reject the write if the key already exists.
                self._client.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=content,
                    ContentType=content_type,
                    IfNoneMatch="*",
                )
            except ClientError as exc:
                raise StorageError(f"s3 put failed for {key}: {exc}") from exc
            except BotoCoreError as exc:
                raise StorageError(f"s3 unavailable: {exc}") from exc

        await asyncio.to_thread(_put)

    async def get(self, key: str) -> bytes:
        def _get() -> bytes:
            try:
                return self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()
            except (ClientError, BotoCoreError) as exc:
                raise StorageError(f"s3 get failed for {key}: {exc}") from exc

        return await asyncio.to_thread(_get)

    async def signed_url(
        self, key: str, *, workspace_id: uuid.UUID, filename: str, ttl_seconds: int
    ) -> str:
        return await asyncio.to_thread(
            self._client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=ttl_seconds,
        )


def build_s3_client(
    *, region: str, endpoint_url: str | None, access_key_id: str | None, secret: str | None
) -> S3Client:
    import boto3

    return boto3.client(
        "s3",
        region_name=region,
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret,
    )
