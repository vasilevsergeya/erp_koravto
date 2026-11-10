import asyncio
import ipaddress
import os
import socket
import uuid
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import aiohttp
import boto3
from aiohttp.abc import AbstractResolver

from app.core.config import config

MAX_FILE_SIZE = 10 * 1024 * 1024
LOCAL_ROOT = Path("/data/files")


def safe_key(key: str) -> str:
    return str(uuid.UUID(key))


def s3_client():
    cfg = config()
    return boto3.client(
        "s3",
        endpoint_url=cfg.s3_endpoint_url or None,
        region_name=cfg.s3_region,
        aws_access_key_id=cfg.s3_access_key_id or None,
        aws_secret_access_key=cfg.s3_secret_access_key or None,
    )


def save_file(content: bytes) -> tuple[str, str]:
    if len(content) > MAX_FILE_SIZE:
        raise ValueError("attachment_too_large")
    cfg = config()
    key = str(uuid.uuid4())
    if cfg.files_backend == "s3":
        if not cfg.s3_bucket:
            raise ValueError("s3_bucket_not_configured")
        s3_client().put_object(Bucket=cfg.s3_bucket, Key="attachments/" + key, Body=content)
    else:
        LOCAL_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
        with os.fdopen(
            os.open(LOCAL_ROOT / key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
        ) as file:
            file.write(content)
    return key, cfg.files_backend


def read_file(key: str, backend: str) -> bytes:
    key = safe_key(key)
    if backend == "s3":
        result = s3_client().get_object(Bucket=config().s3_bucket, Key="attachments/" + key)
        return result["Body"].read(MAX_FILE_SIZE + 1)
    return (LOCAL_ROOT / key).read_bytes()


def validate_public_ip(value: str):
    address = ipaddress.ip_address(value)
    if not address.is_global or (
        address.version == 6 and address.ipv4_mapped and not address.ipv4_mapped.is_global
    ):
        raise ValueError("attachment_private_address")


class PublicResolver(AbstractResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        results = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM, family=family
        )
        output = []
        for fam, _, proto, _, sockaddr in results:
            validate_public_ip(str(sockaddr[0]))
            output.append(
                {
                    "hostname": host,
                    "host": sockaddr[0],
                    "port": port,
                    "family": fam,
                    "proto": proto,
                    "flags": socket.AI_NUMERICHOST,
                }
            )
        return output

    async def close(self):
        pass


async def download_attachment(url: str) -> bytes:
    # The validated resolver pins the connection to public addresses (no DNS rebinding).
    connector = aiohttp.TCPConnector(resolver=PublicResolver(), use_dns_cache=False)
    async with aiohttp.ClientSession(
        connector=connector, timeout=aiohttp.ClientTimeout(total=25), trust_env=False
    ) as client:
        for _ in range(4):
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or parsed.username
                or parsed.password
                or not parsed.hostname
                or parsed.port not in (None, 443)
            ):
                raise ValueError("attachment_invalid_url")
            try:
                address = ipaddress.ip_address(parsed.hostname)
            except ValueError:
                address = None
            if address:
                validate_public_ip(str(address))
            async with client.get(url, allow_redirects=False) as response:
                if response.status in (301, 302, 303, 307, 308):
                    url = urljoin(url, response.headers.get("Location", ""))
                    continue
                if response.status != 200:
                    raise ValueError("attachment_download_failed")
                if response.content_length and response.content_length > MAX_FILE_SIZE:
                    raise ValueError("attachment_too_large")
                content = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    content.extend(chunk)
                    if len(content) > MAX_FILE_SIZE:
                        raise ValueError("attachment_too_large")
                return bytes(content)
        raise ValueError("attachment_redirect_limit")
