from __future__ import annotations

import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, HttpUrl


Resolver = Callable[[str], Awaitable[list[str]]]


class ExternalVacancy(BaseModel):
    url: HttpUrl
    text: str
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class SourcePolicy:
    allowed_domains: frozenset[str]
    max_redirects: int = 3
    max_bytes: int = 1_048_576
    max_text_chars: int = 30_000


async def resolve_public_addresses(host: str) -> list[str]:
    loop = __import__("asyncio").get_running_loop()
    records = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(record[4][0] for record in records))


def _is_public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_global


class SourceFetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        policy: SourcePolicy,
        resolver: Resolver = resolve_public_addresses,
    ) -> None:
        self._client = client
        self._policy = policy
        self._resolver = resolver

    def _domain_allowed(self, host: str) -> bool:
        host = host.casefold().rstrip(".")
        return any(
            host == domain or host.endswith(f".{domain}")
            for domain in self._policy.allowed_domains
        )

    async def _url_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        host = parsed.hostname.casefold()
        if not self._domain_allowed(host):
            return False
        try:
            direct_ip = ipaddress.ip_address(host)
        except ValueError:
            try:
                addresses = await self._resolver(host)
            except (OSError, socket.gaierror):
                return False
        else:
            addresses = [str(direct_ip)]
        return bool(addresses) and all(_is_public_address(item) for item in addresses)

    async def fetch(self, url: str) -> ExternalVacancy | None:
        current_url = url
        for redirect_count in range(self._policy.max_redirects + 1):
            if not await self._url_allowed(current_url):
                return None
            try:
                response = await self._client.get(
                    current_url,
                    follow_redirects=False,
                    timeout=10.0,
                    headers={"user-agent": "JobBot/0.1 (+single-user vacancy reader)"},
                )
            except httpx.HTTPError:
                return None

            if response.is_redirect:
                if redirect_count >= self._policy.max_redirects:
                    return None
                location = response.headers.get("location")
                if not location:
                    return None
                current_url = urljoin(current_url, location)
                continue

            if response.status_code != 200:
                return None
            content_type = response.headers.get("content-type", "").casefold()
            if not (content_type.startswith("text/html") or content_type.startswith("text/plain")):
                return None
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > self._policy.max_bytes:
                return None
            if len(response.content) > self._policy.max_bytes:
                return None

            if content_type.startswith("text/html"):
                soup = BeautifulSoup(response.text, "html.parser")
                for element in soup(["script", "style", "noscript", "svg"]):
                    element.decompose()
                text = " ".join(soup.get_text(" ", strip=True).split())
            else:
                text = " ".join(response.text.split())
            if not text:
                return None
            return ExternalVacancy(
                url=current_url,
                text=text[: self._policy.max_text_chars],
                retrieved_at=datetime.now(timezone.utc),
            )
        return None
