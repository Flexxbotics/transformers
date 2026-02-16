from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional, Union

import boto3
from botocore.client import BaseClient
from botocore.exceptions import BotoCoreError, ClientError

from protocols.abstract_protocol import AbstractProtocol


@dataclass(frozen=True)
class S3Location:
    """
    Represents an S3 destination.

    :param bucket:
        S3 bucket name.
    :param prefix:
        Optional "folder" prefix inside the bucket (e.g. "customer-a/logs").
        May be empty or None.
    :param filename:
        Object filename (e.g. "output.json" or "2026/02/12/run-1.parquet").

    :author: cadenc@flexxbotics.com
    :since:  ODOULS.IP (7.1.15.2)
    """
    bucket: str
    prefix: str
    filename: str

    def key(self) -> str:
        p = (self.prefix or "").strip("/")
        f = (self.filename or "").lstrip("/")
        return f"{p}/{f}" if p else f


class S3Protocol(AbstractProtocol):
    """
    AWS S3 protocol implementation for saving data to an S3 bucket.

    This protocol is intended to be generic: callers can specify bucket, prefix (folder),
    and filename per write. It supports passing bytes, str, or file paths to upload.

    Notes:
    - Uses boto3 (S3 client).
    - "connect" validates credentials and connectivity using STS get_caller_identity
      and optionally verifies access to a default bucket (head_bucket).
    - "send" uploads raw bytes to a configured default location (bucket/prefix/filename)
      unless you use `put_bytes/put_text/put_file` which accept per-call destinations.

    Environment/config you can use:
      - AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN (or IAM role)
      - AWS_REGION / AWS_DEFAULT_REGION
      - Optional: endpoint_url for S3-compatible storage (MinIO, Ceph, etc.)

    :author: cadenc@flexxbotics.com
    :since:  ODOULS.IP (7.1.15.2)
    """

    def __init__(
            self,
            *,
            default_bucket: Optional[str] = None,
            default_prefix: str = "",
            region_name: Optional[str] = None,
            endpoint_url: Optional[str] = None,
            default_content_type: str = "application/octet-stream",
            connect_validate_bucket: bool = False,
            aws_access_key_id: Optional[str] = None,
            aws_secret_access_key: Optional[str] = None,
            aws_session_token: Optional[str] = None
    ):
        super().__init__()

        self._default_bucket = default_bucket
        self._default_prefix = default_prefix or ""
        self._region_name = region_name
        self._endpoint_url = endpoint_url
        self._default_content_type = default_content_type
        self._connect_validate_bucket = connect_validate_bucket

        # ✅ Explicit credential support added here
        self._session = boto3.session.Session(
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_session_token=aws_session_token,
            region_name=region_name,
        )

        self._s3 = None
        self._sts = None

    # -----------------------------
    # AbstractProtocol requirements
    # -----------------------------

    def connect(self) -> int:
        """
        Initialize AWS clients and validate connectivity.

        Returns:
            0 on success, non-zero on failure.

        Validation steps:
          1) Create S3 + STS clients.
          2) Call STS GetCallerIdentity to confirm credentials are usable.
          3) (Optional) If default_bucket is set and connect_validate_bucket=True,
             call S3 HeadBucket to confirm bucket access.

        :author: cadenc@flexxbotics.com
        :since:  ODOULS.IP (7.1.15.2)
        """
        try:
            self._debug(self, "Connecting to AWS S3...")

            self._s3 = self._session.client(
                "s3",
                region_name=self._region_name,
                endpoint_url=self._endpoint_url,
            )
            self._sts = self._session.client(
                "sts",
                region_name=self._region_name,
                endpoint_url=None,  # STS should not use S3 endpoint_url
            )

            ident = self._sts.get_caller_identity()
            arn = ident.get("Arn", "unknown")
            self._info(self, f"Connected to AWS credentials: {arn}")

            if self._connect_validate_bucket and self._default_bucket:
                self._debug(self, f"Validating access to bucket '{self._default_bucket}'...")
                self._s3.head_bucket(Bucket=self._default_bucket)
                self._info(self, f"Bucket access OK: {self._default_bucket}")

            return 0
        except (ClientError, BotoCoreError, Exception) as exc:
            self._error(self, f"Failed to connect to S3: {exc}")
            self._s3 = None
            self._sts = None
            return 1

    def disconnect(self) -> int:
        """
        Disconnect/cleanup.

        boto3 clients do not require an explicit disconnect, but we null references to
        ensure this instance doesn't accidentally reuse stale clients.

        :author: cadenc@flexxbotics.com
        :since:  ODOULS.IP (7.1.15.2)
        """
        self._debug(self, "Disconnecting from AWS S3...")
        self._s3 = None
        self._sts = None
        return 0

    def send(self, data: bytes) -> int:
        """
        Upload raw bytes to the configured default bucket/prefix.

        This is a convenience method to satisfy AbstractProtocol. For more control
        (dynamic bucket/prefix/filename per call), prefer:
          - put_bytes(...)
          - put_text(...)
          - put_file(...)

        Requires:
          - connect() called successfully
          - default_bucket set
          - caller provides a filename via `set_default_filename(...)` or uses `put_*`

        :param data:
            payload as bytes

        :return:
            0 on success, non-zero on failure

        :author: cadenc@flexxbotics.com
        :since:  ODOULS.IP (7.1.15.2)
        """
        # We don't have a buffer/stream semantics for "receive" with S3,
        # so "send" means "write object" to a default location.
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("S3Protocol.send expects bytes")

        if not self._default_bucket:
            self._error(self, "default_bucket is not configured; use put_bytes(...) with a bucket.")
            return 1

        # default filename can be stored on instance
        filename = getattr(self, "_default_filename", None)
        if not filename:
            self._error(self, "default filename not set; call set_default_filename(...) or use put_bytes(...).")
            return 1

        loc = S3Location(bucket=self._default_bucket, prefix=self._default_prefix, filename=filename)
        return self.put_bytes(
            bucket=loc.bucket,
            prefix=loc.prefix,
            filename=loc.filename,
            data=bytes(data),
            content_type=self._default_content_type,
        )

    def receive(self, buffer_size: int) -> str:
        """
        S3 is not a streaming socket protocol, so "receive" is not directly applicable.

        If you need reads, add a `get_text/get_bytes` method. For now, raise.

        :param buffer_size:
            unused for S3

        :return:
            never returns

        :author: cadenc@flexxbotics.com
        :since:  ODOULS.IP (7.1.15.2)
        """
        raise NotImplementedError("S3Protocol.receive is not supported; use explicit get_* methods if needed.")

    # -----------------------------
    # Convenience configuration
    # -----------------------------

    def set_default_filename(self, filename: str) -> None:
        """
        Set the default filename used by `send(...)`.

        :param filename:
            object filename, e.g. "events.json"

        :author: cadenc@flexxbotics.com
        :since:  ODOULS.IP (7.1.15.2)
        """
        self._default_filename = filename

    # -----------------------------
    # Core S3 write helpers
    # -----------------------------

    def put_bytes(
        self,
        *,
        bucket: str,
        prefix: str = "",
        filename: str,
        data: bytes,
        content_type: Optional[str] = None,
        metadata: Optional[dict] = None,
        server_side_encryption: Optional[str] = None,  # e.g., "AES256" or "aws:kms"
        kms_key_id: Optional[str] = None,
        acl: Optional[str] = None,  # e.g., "private"
    ) -> int:
        """
        Upload bytes to S3.

        :param bucket:
            destination bucket
        :param prefix:
            destination folder/prefix in bucket (optional)
        :param filename:
            destination object name
        :param data:
            payload bytes
        :param content_type:
            Content-Type for the object. Defaults to instance default.
        :param metadata:
            Optional S3 user-defined metadata dict (string->string).
        :param server_side_encryption:
            Optional SSE setting ("AES256" or "aws:kms")
        :param kms_key_id:
            Optional KMS Key ID/ARN if using aws:kms
        :param acl:
            Optional canned ACL (generally keep None/private)

        :return:
            0 on success, non-zero on failure

        :author: cadenc@flexxbotics.com
        :since:  ODOULS.IP (7.1.15.2)
        """
        if self._s3 is None:
            self._error(self, "S3 client not initialized; call connect() first.")
            return 1

        loc = S3Location(bucket=bucket, prefix=prefix, filename=filename)
        key = loc.key()

        try:
            extra_args = {
                "Bucket": bucket,
                "Key": key,
                "Body": data,
                "ContentType": content_type or self._default_content_type,
            }

            if metadata:
                extra_args["Metadata"] = {str(k): str(v) for k, v in metadata.items()}

            if server_side_encryption:
                extra_args["ServerSideEncryption"] = server_side_encryption
            if kms_key_id:
                extra_args["SSEKMSKeyId"] = kms_key_id
            if acl:
                extra_args["ACL"] = acl

            self._debug(self, f"Uploading {len(data)} bytes to s3://{bucket}/{key}")
            self._s3.put_object(**extra_args)
            self._info(self, f"Uploaded to s3://{bucket}/{key}")
            return 0

        except (ClientError, BotoCoreError, Exception) as exc:
            self._error(self, f"Failed to upload to s3://{bucket}/{key}: {exc}")
            return 1

    def put_text(
        self,
        *,
        bucket: str,
        prefix: str = "",
        filename: str,
        text: str,
        encoding: str = "utf-8",
        content_type: str = "text/plain; charset=utf-8",
        metadata: Optional[dict] = None,
        server_side_encryption: Optional[str] = None,
        kms_key_id: Optional[str] = None,
        acl: Optional[str] = None,
    ) -> int:
        """
        Upload text to S3.

        :param text:
            payload as a string (encoded to bytes)

        :author: cadenc@flexxbotics.com
        :since:  ODOULS.IP (7.1.15.2)
        """
        return self.put_bytes(
            bucket=bucket,
            prefix=prefix,
            filename=filename,
            data=text.encode(encoding),
            content_type=content_type,
            metadata=metadata,
            server_side_encryption=server_side_encryption,
            kms_key_id=kms_key_id,
            acl=acl,
        )

    def put_file(
        self,
        *,
        bucket: str,
        prefix: str = "",
        filename: str,
        file_path: str,
        content_type: Optional[str] = None,
        metadata: Optional[dict] = None,
        server_side_encryption: Optional[str] = None,
        kms_key_id: Optional[str] = None,
        acl: Optional[str] = None,
    ) -> int:
        """
        Upload a local file to S3.

        :param file_path:
            local file path to upload

        :author: cadenc@flexxbotics.com
        :since:  ODOULS.IP (7.1.15.2)
        """
        if self._s3 is None:
            self._error(self, "S3 client not initialized; call connect() first.")
            return 1

        loc = S3Location(bucket=bucket, prefix=prefix, filename=filename)
        key = loc.key()

        if not os.path.isfile(file_path):
            self._error(self, f"File not found: {file_path}")
            return 1

        try:
            extra = {}
            if content_type:
                extra["ContentType"] = content_type
            if metadata:
                extra["Metadata"] = {str(k): str(v) for k, v in metadata.items()}
            if server_side_encryption:
                extra["ServerSideEncryption"] = server_side_encryption
            if kms_key_id:
                extra["SSEKMSKeyId"] = kms_key_id
            if acl:
                extra["ACL"] = acl

            self._debug(self, f"Uploading file '{file_path}' to s3://{bucket}/{key}")
            # boto3's upload_file uses managed transfer + multipart where needed
            self._s3.upload_file(
                Filename=file_path,
                Bucket=bucket,
                Key=key,
                ExtraArgs=extra if extra else None,
            )
            self._info(self, f"Uploaded file to s3://{bucket}/{key}")
            return 0

        except (ClientError, BotoCoreError, Exception) as exc:
            self._error(self, f"Failed to upload file to s3://{bucket}/{key}: {exc}")
            return 1

    # -----------------------------
    # Optional helpers
    # -----------------------------

    @staticmethod
    def safe_prefix(*parts: str) -> str:
        """
        Create a safe S3 prefix from multiple path parts.

        Example:
            safe_prefix("customer-a", "logs", "2026-02-12") -> "customer-a/logs/2026-02-12"

        :author: cadenc@flexxbotics.com
        :since:  ODOULS.IP (7.1.15.2)
        """
        cleaned = []
        for p in parts:
            if not p:
                continue
            p = str(p).strip().strip("/")
            if p:
                cleaned.append(p)
        return "/".join(cleaned)

    @staticmethod
    def safe_filename(name: str, *, default: str = "data.bin") -> str:
        """
        Sanitize a filename for S3 object keys (does not remove slashes if you want nested paths).

        :param name:
            proposed filename or key suffix
        :param default:
            used if name is empty

        :author: cadenc@flexxbotics.com
        :since:  ODOULS.IP (7.1.15.2)
        """
        if not name:
            return default
        name = str(name).strip()
        # Remove control characters; keep common filename chars + slashes for nested paths
        name = re.sub(r"[\x00-\x1f\x7f]", "", name)
        return name or default
