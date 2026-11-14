"""Regression test: B2C cookies go back to the server exactly as they were set.

Azure B2C's x-ms-cpim-* cookies are base64 with "=", "/" and "+", and some names
contain "|". When aiohttp quoted the values, B2C saw a CSRF cookie that did not match
the X-CSRF-TOKEN header and the sign-in failed at SelfAsserted.
"""

from __future__ import annotations

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from custom_components.mijnted.api import create_cookie_jar

CSRF_VALUE = "aGVsbG8+d29ybGQ/Zm9v/YmFy+cXV4=="
CACHE_NAME = "x-ms-cpim-cache|abc_0"
CACHE_VALUE = "eyJUSUQiOiJmYWtlIn0=/a+b=="
TRANS_VALUE = "dHJhbnM=+/"


async def set_cookies(_request: web.Request) -> web.Response:
    """Set B2C-like cookies with raw Set-Cookie headers, as B2C does."""
    response = web.Response(text="ok")
    response.headers.add("Set-Cookie", f"x-ms-cpim-csrf={CSRF_VALUE}; path=/")
    response.headers.add("Set-Cookie", f"{CACHE_NAME}={CACHE_VALUE}; path=/")
    response.headers.add("Set-Cookie", f"x-ms-cpim-trans={TRANS_VALUE}; path=/")
    return response


async def echo_cookies(request: web.Request) -> web.Response:
    """Return the Cookie header the client sent."""
    return web.Response(text=request.headers.get("Cookie", ""))


@pytest.mark.usefixtures("socket_enabled")
async def test_b2c_cookies_are_sent_verbatim() -> None:
    """Test the Cookie header holds the B2C values byte for byte, unquoted."""
    app = web.Application()
    app.router.add_get("/set", set_cookies)
    app.router.add_get("/echo", echo_cookies)
    async with (
        TestServer(app, host="127.0.0.1") as server,
        ClientSession(cookie_jar=create_cookie_jar()) as session,
    ):
        # A host name, not an IP address: the jar only keeps cookies of named hosts.
        base = f"http://localhost:{server.port}"
        async with session.get(f"{base}/set") as response:
            assert response.status == 200
        async with session.get(f"{base}/echo") as response:
            sent = await response.text()

    cookies = dict(part.split("=", 1) for part in sent.split("; "))
    assert cookies["x-ms-cpim-csrf"] == CSRF_VALUE
    assert cookies[CACHE_NAME] == CACHE_VALUE
    assert cookies["x-ms-cpim-trans"] == TRANS_VALUE
    assert '"' not in sent
