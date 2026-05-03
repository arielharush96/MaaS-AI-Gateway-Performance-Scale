"""
Lightweight reverse proxy that authenticates to the AI Gateway via OAuth
cookie flow, then forwards all requests with the session cookie injected.

Usage:
    python gateway_auth_proxy.py \
        --gateway-url https://data-science-gateway.apps.<cluster> \
        --username perfuser --password perfpass123 \
        --listen-port 9090

Then point GuideLLM at http://localhost:9090
"""

import argparse
import asyncio
import http.cookies
import re
import ssl
import sys
import time

import aiohttp
from aiohttp import web


def parse_args():
    p = argparse.ArgumentParser(description="Auth-cookie proxy for AI Gateway")
    p.add_argument("--gateway-url", required=True, help="AI Gateway external URL")
    p.add_argument("--username", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--listen-port", type=int, default=9090)
    p.add_argument("--cookie-refresh-margin", type=int, default=3600,
                   help="Refresh cookie this many seconds before expiry")
    return p.parse_args()


class CookieProxy:
    def __init__(self, gateway_url: str, username: str, password: str,
                 refresh_margin: int = 3600):
        self.gateway_url = gateway_url.rstrip("/")
        self.username = username
        self.password = password
        self.refresh_margin = refresh_margin
        self.cookies: dict[str, str] = {}
        self.cookie_expiry = 0.0
        self._lock = asyncio.Lock()
        self._ssl_ctx = ssl.create_default_context()
        self._ssl_ctx.check_hostname = False
        self._ssl_ctx.verify_mode = ssl.CERT_NONE

    async def _do_oauth_flow(self) -> dict[str, str]:
        """Perform the 3-step OAuth cookie flow."""
        jar = aiohttp.CookieJar(unsafe=True)
        conn = aiohttp.TCPConnector(ssl=self._ssl_ctx)
        async with aiohttp.ClientSession(cookie_jar=jar, connector=conn) as s:
            # Step 1: hit gateway → get OAuth redirect URL
            async with s.get(
                f"{self.gateway_url}/v1/models",
                allow_redirects=False,
            ) as r1:
                redir = r1.headers.get("Location", "")
                if not redir or "oauth" not in redir:
                    raise RuntimeError(f"Step 1 failed: no OAuth redirect (status={r1.status})")

            # Step 2: hit OAuth server with challenge auth → get callback URL
            auth = aiohttp.BasicAuth(self.username, self.password)
            async with s.get(
                redir,
                headers={"X-CSRF-Token": "1"},
                auth=auth,
                allow_redirects=False,
            ) as r2:
                callback = r2.headers.get("Location", "")
                if not callback or "callback" not in callback:
                    raise RuntimeError(f"Step 2 failed: no callback URL (status={r2.status})")

            # Step 3: hit callback → session cookie is set
            async with s.get(callback, allow_redirects=False) as r3:
                if r3.status not in (200, 302):
                    raise RuntimeError(f"Step 3 failed: status={r3.status}")

        cookies = {}
        for cookie in jar:
            cookies[cookie.key] = cookie.value
        if not cookies:
            raise RuntimeError("OAuth flow completed but no cookies received")
        return cookies

    async def ensure_cookie(self):
        async with self._lock:
            if time.time() < self.cookie_expiry - self.refresh_margin:
                return
            print(f"[proxy] Refreshing auth cookie...", flush=True)
            self.cookies = await self._do_oauth_flow()
            self.cookie_expiry = time.time() + 86400  # 24h
            print(f"[proxy] Cookie refreshed, {len(self.cookies)} cookie(s) acquired", flush=True)

    def _cookie_header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    async def handle(self, request: web.Request) -> web.StreamResponse:
        await self.ensure_cookie()

        target = f"{self.gateway_url}{request.path_qs}"
        headers = dict(request.headers)
        headers.pop("Host", None)
        headers.pop("host", None)
        headers["Cookie"] = self._cookie_header()

        body = await request.read()
        conn = aiohttp.TCPConnector(ssl=self._ssl_ctx)

        async with aiohttp.ClientSession(connector=conn) as session:
            async with session.request(
                method=request.method,
                url=target,
                headers=headers,
                data=body,
            ) as upstream:
                is_streaming = "text/event-stream" in upstream.headers.get("Content-Type", "")

                resp_headers = {}
                for k, v in upstream.headers.items():
                    lk = k.lower()
                    if lk not in ("transfer-encoding", "content-encoding", "content-length"):
                        resp_headers[k] = v

                if is_streaming:
                    response = web.StreamResponse(
                        status=upstream.status,
                        headers=resp_headers,
                    )
                    response.content_type = "text/event-stream"
                    await response.prepare(request)
                    async for chunk in upstream.content.iter_any():
                        await response.write(chunk)
                    await response.write_eof()
                    return response
                else:
                    body_data = await upstream.read()
                    return web.Response(
                        status=upstream.status,
                        headers=resp_headers,
                        body=body_data,
                    )


async def main():
    args = parse_args()
    proxy = CookieProxy(args.gateway_url, args.username, args.password,
                        args.cookie_refresh_margin)

    await proxy.ensure_cookie()

    app = web.Application()
    app.router.add_route("*", "/{path_info:.*}", proxy.handle)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", args.listen_port)
    await site.start()
    print(f"[proxy] Listening on http://0.0.0.0:{args.listen_port} → {args.gateway_url}", flush=True)

    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
