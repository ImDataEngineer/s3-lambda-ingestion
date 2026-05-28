"""boto3 client factory — LocalStack-aware.

Why this module exists
----------------------
A learner who calls `boto3.client("s3")` directly in their test code will, on a
laptop with real AWS credentials in `~/.aws/credentials`, accidentally hit
production AWS. Or worse, on CI, hit a wrong region.

This helper makes the choice EXPLICIT:

  - If `AWS_ENDPOINT_URL` is set, every client points there (LocalStack).
  - Otherwise it falls back to default boto3 resolution (real AWS).

Use it everywhere — tests, Lambda packaging scripts, any local utility. The
Lambda function itself, when it runs inside LocalStack's Lambda container, uses
the same logic via the `AWS_ENDPOINT_URL` environment variable that LocalStack
injects automatically into runtime containers.

Default credentials are also forced to dummy values when targeting LocalStack
so a misconfigured laptop can't sign requests to real AWS by accident.
"""

from __future__ import annotations

import os
from typing import Any

import boto3
from botocore.config import Config

DEFAULT_LOCALSTACK_ENDPOINT = "http://localhost:4566"
DEFAULT_REGION = "us-east-1"


def _is_localstack() -> bool:
    return bool(os.environ.get("AWS_ENDPOINT_URL"))


def _localstack_endpoint() -> str:
    return os.environ.get("AWS_ENDPOINT_URL", DEFAULT_LOCALSTACK_ENDPOINT)


def get_client(service_name: str, **extra: Any):
    """Return a boto3 client wired to LocalStack when `AWS_ENDPOINT_URL` is set.

    `extra` is forwarded to `boto3.client()` so callers can override region or
    config if needed.
    """
    if _is_localstack():
        return boto3.client(
            service_name,
            endpoint_url=_localstack_endpoint(),
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
            region_name=os.environ.get("AWS_DEFAULT_REGION", DEFAULT_REGION),
            config=Config(retries={"max_attempts": 3, "mode": "standard"}),
            **extra,
        )
    # Real AWS path — relies on the standard credential chain.
    return boto3.client(service_name, **extra)


def get_resource(service_name: str, **extra: Any):
    """Same idea as `get_client` but for the resource interface (DynamoDB Table, etc.)."""
    if _is_localstack():
        return boto3.resource(
            service_name,
            endpoint_url=_localstack_endpoint(),
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
            region_name=os.environ.get("AWS_DEFAULT_REGION", DEFAULT_REGION),
            **extra,
        )
    return boto3.resource(service_name, **extra)
