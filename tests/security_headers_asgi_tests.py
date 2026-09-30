"""The security headers are added by a plain ASGI middleware (keepup-88).

Written on BaseHTTPMiddleware, the middleware ran every request through a task
and a body stream of its own -- the most expensive kind of middleware Starlette
has, part of the 7% the middleware chain took on the load stand (keepup-53).
It only ever added headers to the start of a response, which plain ASGI does
at no such cost.

    python3 -m pytest keepup/tests/security_headers_asgi_tests.py -v
"""

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from keepup.factory import SecurityHeadersMiddleware


def app_with(https=False):
    app = FastAPI()

    @app.get("/plain")
    def plain():
        return PlainTextResponse("ok")

    @app.get("/framed")
    def framed():
        return PlainTextResponse("ok", headers={"X-Frame-Options": "DENY"})

    @app.get("/stream")
    def stream():
        return StreamingResponse(iter([b"a", b"b"]))

    app.add_middleware(SecurityHeadersMiddleware, https=https)
    return TestClient(app)


def test_it_is_not_a_base_http_middleware():
    assert not issubclass(SecurityHeadersMiddleware, BaseHTTPMiddleware)


def test_the_headers_are_on_every_response():
    answer = app_with().get("/plain")
    assert answer.headers["x-frame-options"] == "SAMEORIGIN"
    assert answer.headers["x-content-type-options"] == "nosniff"
    assert answer.headers["referrer-policy"] == "same-origin"
    assert "strict-transport-security" not in answer.headers


def test_a_header_the_response_set_is_kept():
    assert app_with().get("/framed").headers["x-frame-options"] == "DENY"


def test_hsts_only_over_tls():
    assert app_with(https=True).get("/plain").headers["strict-transport-security"] == "max-age=31536000"


def test_a_streamed_body_arrives_whole():
    answer = app_with().get("/stream")
    assert answer.text == "ab" and answer.headers["x-frame-options"] == "SAMEORIGIN"
