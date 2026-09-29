"""Evidence object store (MinIO in dev, any S3 in production).

Two jobs: fetch an object so ingest can recompute its SHA-256 (chain of custody, docs/08 §7),
and mint short-lived signed URLs so the frontend can show a crop without the bucket being
public.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Protocol
from urllib.parse import urlparse

import structlog

from argus_api.core.config import get_settings

log = structlog.get_logger()


class ObjectMissing(Exception):
    """The object isn't there (yet). Incident clips legitimately arrive overnight."""


class StoreUnavailable(Exception):
    """The store itself could not be reached."""


def split_uri(uri: str) -> tuple[str, str]:
    u = urlparse(uri)
    if u.scheme != "s3" or not u.netloc or not u.path.strip("/"):
        raise ValueError(f"not an s3:// URI: {uri}")
    return u.netloc, u.path.lstrip("/")


class EvidenceStore(Protocol):
    def sha256(self, uri: str) -> str: ...
    def signed_url(self, uri: str, ttl_s: int) -> str: ...
    def delete(self, uri: str) -> None: ...


class S3Store:
    def __init__(self) -> None:
        import boto3
        from botocore.config import Config

        s = get_settings()
        self._client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key,
            region_name=s.s3_region,
            config=Config(
                connect_timeout=3,
                read_timeout=10,
                retries={"max_attempts": 2},
                signature_version="s3v4",
            ),
        )

    def sha256(self, uri: str) -> str:
        from botocore.exceptions import BotoCoreError, ClientError

        bucket, key = split_uri(uri)
        try:
            body = self._client.get_object(Bucket=bucket, Key=key)["Body"]
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404", "NotFound", "NoSuchBucket"):
                raise ObjectMissing(uri) from exc
            raise StoreUnavailable(str(exc)) from exc
        except BotoCoreError as exc:
            raise StoreUnavailable(str(exc)) from exc
        h = hashlib.sha256()
        for chunk in iter(lambda: body.read(1 << 16), b""):
            h.update(chunk)
        return h.hexdigest()

    def signed_url(self, uri: str, ttl_s: int) -> str:
        bucket, key = split_uri(uri)
        return self._client.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=ttl_s
        )

    def delete(self, uri: str) -> None:
        bucket, key = split_uri(uri)
        self._client.delete_object(Bucket=bucket, Key=key)


class MemoryStore:
    """For tests and for replaying logs without MinIO."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, uri: str, data: bytes) -> str:
        self.objects[uri] = data
        return hashlib.sha256(data).hexdigest()

    def sha256(self, uri: str) -> str:
        if uri not in self.objects:
            raise ObjectMissing(uri)
        return hashlib.sha256(self.objects[uri]).hexdigest()

    def signed_url(self, uri: str, ttl_s: int) -> str:
        bucket, key = split_uri(uri)
        return f"http://evidence.local/{bucket}/{key}?ttl={ttl_s}"

    def delete(self, uri: str) -> None:
        self.objects.pop(uri, None)


_override: EvidenceStore | None = None


def set_store(store: EvidenceStore | None) -> None:
    """Test hook."""
    global _override
    _override = store
    _default_store.cache_clear()


@lru_cache
def _default_store() -> EvidenceStore:
    return S3Store()


def get_store() -> EvidenceStore:
    return _override or _default_store()
