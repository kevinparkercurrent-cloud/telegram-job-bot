import httpx
import pytest

from job_bot.sources import SourceFetcher, SourcePolicy


async def public_resolver(host: str) -> list[str]:
    return ["93.184.216.34"]


async def mixed_resolver(host: str) -> list[str]:
    if host == "internal.example":
        return ["127.0.0.1"]
    return ["93.184.216.34"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    ("http://127.0.0.1/job", "http://169.254.169.254/latest/meta-data"),
)
async def test_rejects_private_and_link_local_targets(url) -> None:
    async with httpx.AsyncClient() as client:
        fetcher = SourceFetcher(
            client,
            SourcePolicy(allowed_domains=frozenset({"127.0.0.1", "169.254.169.254"})),
            public_resolver,
        )
        assert await fetcher.fetch(url) is None


@pytest.mark.asyncio
async def test_extracts_visible_text_from_allowed_html(respx_mock) -> None:
    respx_mock.get("https://jobs.example/v/7").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html><script>secret()</script><main><h1>Project Manager</h1><p>Remote role</p></main></html>",
        )
    )
    async with httpx.AsyncClient() as client:
        fetcher = SourceFetcher(
            client,
            SourcePolicy(allowed_domains=frozenset({"jobs.example"})),
            public_resolver,
        )
        result = await fetcher.fetch("https://jobs.example/v/7")
    assert result is not None
    assert result.text == "Project Manager Remote role"
    assert "secret" not in result.text


@pytest.mark.asyncio
async def test_rejects_non_html_and_oversized_body(respx_mock) -> None:
    respx_mock.get("https://jobs.example/file").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=b"x",
        )
    )
    respx_mock.get("https://jobs.example/large").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"x" * 1_048_577,
        )
    )
    async with httpx.AsyncClient() as client:
        fetcher = SourceFetcher(
            client,
            SourcePolicy(allowed_domains=frozenset({"jobs.example"})),
            public_resolver,
        )
        assert await fetcher.fetch("https://jobs.example/file") is None
        assert await fetcher.fetch("https://jobs.example/large") is None


@pytest.mark.asyncio
async def test_redirect_to_private_target_is_rejected(respx_mock) -> None:
    respx_mock.get("https://jobs.example/go").mock(
        return_value=httpx.Response(302, headers={"location": "http://internal.example/job"})
    )
    async with httpx.AsyncClient() as client:
        fetcher = SourceFetcher(
            client,
            SourcePolicy(
                allowed_domains=frozenset({"jobs.example", "internal.example"})
            ),
            mixed_resolver,
        )
        assert await fetcher.fetch("https://jobs.example/go") is None

