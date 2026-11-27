"""A REST connection from a request that already works: a URL, or a curl command copied from API
docs, Postman or a browser's network tab.

It splits the URL into the API's base and the request's path and parameters, and recognises how
the request signs in (a bearer token, an API key in a header or the query, a user and password).
The secret it finds is handed back separately, to be kept in the project's .env, never in
murakumo.yaml.
"""

from __future__ import annotations

import base64
import re
import shlex
from typing import Any
from urllib.parse import parse_qsl, urlsplit

KEY_PARAMS = {"key", "api_key", "apikey", "api-key", "access_token", "token", "appid", "app_key", "subscription-key"}
KEY_HEADERS = {"x-api-key", "api-key", "apikey", "x-auth-token", "ocp-apim-subscription-key", "x-functions-key"}
SKIP_HEADERS = {"accept", "content-type", "user-agent", "accept-encoding", "connection", "host", "content-length"}


class PastedRequestError(ValueError):
    pass


def parse(text: str) -> dict[str, Any]:
    """{connection: {base_url, headers, auth}, endpoint: {path, method, params, table}, secret: value or
    None, dynamics: bool}."""
    text = (text or "").strip()
    if not text:
        raise PastedRequestError("paste a URL, or a curl command, that returns the data")
    url, method, headers, user = _curl(text) if text.split(None, 1)[0].lower() == "curl" else (text, "GET", {}, None)
    if not re.match(r"^https?://", url):
        raise PastedRequestError(f"{url[:80]!r} is not a URL starting with https://")
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]
    base_path = "/" + "/".join(segments[:-1]) if len(segments) > 1 else ""
    path = "/" + segments[-1] if segments else ""
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    auth: dict[str, Any] = {"type": "none"}
    secret: str | None = None

    kept: dict[str, str] = {}
    for name, value in headers.items():
        lower = name.lower()
        if lower == "authorization":
            scheme, _, credential = value.partition(" ")
            if scheme.lower() == "bearer" and credential:
                auth, secret = {"type": "bearer"}, credential.strip()
            elif scheme.lower() == "basic" and credential:
                user = _basic(credential)
            else:
                auth, secret = {"type": "api_key", "name": name, "location": "header"}, value
        elif lower in KEY_HEADERS:
            auth, secret = {"type": "api_key", "name": name, "location": "header"}, value
        elif lower not in SKIP_HEADERS and not lower.startswith(("sec-", "cookie")):
            kept[name] = value
    if user is not None and auth["type"] == "none":
        auth, secret = {"type": "basic"}, user
    if auth["type"] == "none":
        for name in list(params):
            if name.lower() in KEY_PARAMS:
                auth, secret = {"type": "api_key", "name": name, "location": "query"}, params.pop(name)
                break

    table = re.sub(r"[^a-z0-9_]+", "_", (segments[-1] if segments else parts.hostname or "data").lower()).strip("_")
    return {
        "connection": {
            "kind": "rest",
            "base_url": f"{parts.scheme}://{parts.netloc}{base_path}",
            **({"headers": kept} if kept else {}),
            "auth": auth,
            "paging": {"type": "none"},
        },
        "endpoint": {
            "table": table if table and not table[0].isdigit() else f"t_{table}",
            "path": path,
            "method": method,
            "params": params,
        },
        "secret": secret,
        "dynamics": ".dynamics.com" in (parts.hostname or ""),
    }


def _curl(text: str) -> tuple[str, str, dict[str, str], str | None]:
    try:
        words = shlex.split(text.replace("\\\n", " "))
    except ValueError as exc:
        raise PastedRequestError(f"the curl command could not be read: {exc}") from None
    url, method, headers, user = None, "GET", {}, None
    i = 1
    while i < len(words):
        word = words[i]
        following = words[i + 1] if i + 1 < len(words) else ""
        if word in ("-H", "--header"):
            name, _, value = following.partition(":")
            headers[name.strip()] = value.strip()
            i += 2
        elif word in ("-X", "--request"):
            method = following.upper()
            i += 2
        elif word in ("-u", "--user"):
            user = following
            i += 2
        elif word in ("-d", "--data", "--data-raw", "--data-binary", "--json"):
            method = "POST" if method == "GET" else method
            i += 2
        elif word == "--url":
            url = following
            i += 2
        elif word.startswith("-"):
            i += 2 if word in ("-o", "--output", "-A", "--user-agent", "-e", "--referer", "-b", "--cookie") else 1
        else:
            url = url or word
            i += 1
    if not url:
        raise PastedRequestError("the curl command has no URL")
    if method not in ("GET", "POST"):
        raise PastedRequestError(f"only GET and POST requests fetch data; this one is {method}")
    return url, method, headers, user


def _basic(credential: str) -> str | None:
    try:
        return base64.b64decode(credential).decode()
    except ValueError:
        return None


__all__ = ["PastedRequestError", "parse"]
