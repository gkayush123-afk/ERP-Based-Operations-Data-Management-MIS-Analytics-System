"""Minimal gzip middleware using only the standard library.

Compresses text-like responses when the client advertises gzip support.
Streaming responses and already-encoded responses are left untouched.
"""

import gzip

COMPRESSIBLE_PREFIXES = (
    "text/",
    "application/json",
    "application/javascript",
    "application/xml",
    "image/svg+xml",
)

MIN_SIZE_BYTES = 500


class GzipMiddleware:
    def __init__(self, app, minimum_size=MIN_SIZE_BYTES):
        self.app = app
        self.minimum_size = minimum_size

    def __call__(self, environ, start_response):
        if "gzip" not in environ.get("HTTP_ACCEPT_ENCODING", ""):
            return self.app(environ, start_response)

        captured = {}

        def capturing_start_response(status, headers, exc_info=None):
            captured["status"] = status
            captured["headers"] = headers
            return lambda data: None

        body = b"".join(self.app(environ, capturing_start_response))
        headers = dict(captured["headers"])

        content_type = headers.get("Content-Type", "")
        if (
            headers.get("Content-Encoding")
            or not content_type.startswith(COMPRESSIBLE_PREFIXES)
            or len(body) < self.minimum_size
        ):
            start_response(captured["status"], captured["headers"])
            return [body]

        compressed = gzip.compress(body, compresslevel=5)
        new_headers = [
            (key, value)
            for key, value in captured["headers"]
            if key.lower() not in {"content-length", "content-encoding"}
        ]
        new_headers.append(("Content-Encoding", "gzip"))
        new_headers.append(("Content-Length", str(len(compressed))))
        new_headers.append(("Vary", "Accept-Encoding"))
        start_response(captured["status"], new_headers)
        return [compressed]
