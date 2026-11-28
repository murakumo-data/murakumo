"""Microsoft Dynamics 365 and Dataverse, from four things: the environment's URL, the tenant, the
app's client id and its secret.

It writes an ordinary REST connection (nothing here is needed to run it): the Web API at
``<url>/api/data/v9.2``, signed in with OAuth client credentials at Microsoft's v2 token endpoint
for the scope ``<url>/.default``, following ``@odata.nextLink`` until every row is read, and
asking only for what changed since the last run (``$filter=modifiedon ge <latest>``).

The app must be an *application user* in the environment (Power Platform admin center), with a
security role that can read the tables; otherwise Dynamics answers 403.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from murakumo import extract, secret_file

API = "/api/data/v9.2"
WATERMARK = "modifiedon"
PAGE = 5000  # the most Dynamics sends a page
LOGIN = "https://login.microsoftonline.com"
NEXT = "@odata.nextLink"
LABELS = 'odata.include-annotations="OData.Community.Display.V1.FormattedValue"'  # a choice's label beside its number


class DynamicsError(ValueError):
    pass


def host(url: str) -> str:
    """https://org.crm11.dynamics.com from whatever was pasted: with or without https://, a path,
    the Web API's address or the address of the app in a browser."""
    text = (url or "").strip()
    if not text:
        raise DynamicsError("give the environment's URL, such as https://yourorg.crm.dynamics.com")
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    name = (parsed.hostname or "").lower()
    if not name or "." not in name:
        raise DynamicsError(f"{url!r} is not a URL: give one such as https://yourorg.crm.dynamics.com")
    scheme = parsed.scheme if parsed.scheme == "http" and name in ("127.0.0.1", "localhost") else "https"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{scheme}://{name}{port}"


def tenant(value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise DynamicsError("give the tenant id (a GUID) or the tenant's domain, such as contoso.onmicrosoft.com")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", text):
        raise DynamicsError(f"{value!r} is not a tenant id: a GUID, or a domain such as contoso.onmicrosoft.com")
    return text


def variables(source: str) -> tuple[str, str]:
    """The variables the client id and secret are kept in."""
    return secret_file.variable(source, "client_id"), secret_file.variable(source, "client_secret")


def connection(source: str, url: str, tenant_id: str, endpoints: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    base = host(url)
    client_id, client_secret = variables(source)
    return {
        "kind": "rest",
        "base_url": base + API,
        "headers": {"Prefer": f"odata.maxpagesize={PAGE},{LABELS}", "OData-MaxVersion": "4.0", "OData-Version": "4.0"},
        "auth": {
            "type": "oauth2",
            "token_url": f"{LOGIN}/{tenant(tenant_id)}/oauth2/v2.0/token",
            "client_id_env": client_id,
            "client_secret_env": client_secret,
            "scope": base + "/.default",
        },
        "paging": {"type": "next_url", "next_path": NEXT},
        "endpoints": endpoints or [],
    }


def with_labels(conn: dict[str, Any]) -> dict[str, Any]:
    """The connection, asking Dynamics for each value's label too (Approved beside 100006). One
    Prefer header carries every preference, so the labels are added to the one already there."""
    headers = dict(conn.get("headers") or {})
    name = next((k for k in headers if k.lower() == "prefer"), "Prefer")
    current = str(headers.get(name) or "")
    if "include-annotations" in current:
        return conn
    headers[name] = f"{current},{LABELS}" if current.strip() else f"odata.maxpagesize={PAGE},{LABELS}"
    return {**conn, "headers": headers}


def is_label(key: str) -> bool:
    return key.endswith(extract.LABEL) and len(key) > len(extract.LABEL)


def labelled(key: str) -> str:
    """The column a label belongs to: statuscode for statuscode@OData.Community.Display.V1.FormattedValue."""
    return key[: -len(extract.LABEL)] if is_label(key) else key


_CODES = re.compile(r"^-?\d+(,-?\d+)*$")


def worth_a_label(key: str, values: list[Any], labels: list[Any]) -> bool:
    """Whether a column's labels say more than its values: a choice (100006 is Approved), a yes or
    no, several choices, or a lookup (a GUID, and the name of what it points to). Dynamics labels
    dates, money and numbers too, only formatted (1,000.00): those are left out."""
    pairs = [(v, lab) for v, lab in zip(values, labels, strict=False) if v is not None and lab not in (None, "")]
    if not pairs:
        return False
    if key.startswith("_") and key.endswith("_value"):
        return True
    for value, label in pairs:
        if isinstance(value, bool):
            return True
        if isinstance(value, int) or (isinstance(value, str) and _CODES.match(value)):
            if re.sub(r"[\s,.'\u00a0\u202f-]", "", str(label)) != re.sub(r"[,-]", "", str(value)):
                return True
    return False


def useful_labels(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows to make a whole raw table from: each label worth having named <column>_label, as it lands;
    the rest (a number's or a date's, only formatted) left out."""
    keys = {k for r in rows for k in r if is_label(k)}
    if not keys:
        return rows
    kept = {k for k in keys if worth_a_label(labelled(k), [r.get(labelled(k)) for r in rows], [r.get(k) for r in rows])}
    return [
        {(extract.column_name(k) if k in kept else k): v for k, v in r.items() if k not in keys or k in kept}
        for r in rows
    ]


def is_dynamics(conn: dict[str, Any] | None) -> bool:
    conn = conn or {}
    base = str(conn.get("base_url") or "")
    return base.endswith(API) and (conn.get("paging") or {}).get("next_path") == NEXT


def settings_of(conn: dict[str, Any]) -> dict[str, str]:
    """The URL and tenant a Dynamics connection was made from, to show them again."""
    token_url = str((conn.get("auth") or {}).get("token_url") or "")
    found = re.search(r"/([^/]+)/oauth2/v2\.0/token$", token_url)
    return {"url": str(conn.get("base_url", "")).removesuffix(API), "tenant": found.group(1) if found else ""}


def endpoint(entity_set: str, watermark: bool = True) -> dict[str, Any]:
    """The request for one table: every row the first time, then what changed since."""
    spec: dict[str, Any] = {"table": table_name(entity_set), "path": f"/{entity_set}", "records": "value"}
    if watermark:
        spec["since_param"] = "$filter"
        spec["since_format"] = f"{WATERMARK} ge {{since}}"
    return spec


def primary_id(names: list[str], entity_set: str, logical_name: str | None = None) -> str | None:
    """The column holding each row's id: the logical name and "id" (contactid, vrp_mortgageloanid)."""
    present = {n.lower(): n for n in names}
    guesses = [f"{logical_name}id"] if logical_name else []
    stem = entity_set.lower()
    if stem.endswith("ies"):
        guesses.append(stem[:-3] + "yid")
    if stem.endswith("es"):
        guesses.append(stem[:-2] + "id")
    if stem.endswith("s"):
        guesses.append(stem[:-1] + "id")
    return next((present[g.lower()] for g in guesses if g.lower() in present), None)


def shape(entity: Any, entity_set: str, logical_name: str | None = None) -> None:
    """How a Dynamics table loads. Each fetch after the first brings only the rows that changed, so the
    raw table must keep what it has: merged by the row's id (the latest by modifiedon wins), or, with
    no id to merge by, added to."""
    from murakumo.ir.model import KeyRole, LoadPattern

    names = [a.name for a in entity.attributes]
    if WATERMARK in names and not entity.watermark:
        entity.watermark = WATERMARK
    key = primary_id(names, entity_set, logical_name)
    for attribute in entity.attributes:
        if attribute.name == key:
            attribute.key = KeyRole.PRIMARY
            attribute.nullable = False
    entity.load = LoadPattern.INCREMENTAL if key else LoadPattern.APPEND


def table_name(entity_set: str) -> str:
    name = re.sub(r"[^a-z0-9_]+", "_", entity_set.lower()).strip("_")
    return name if name and not name[0].isdigit() else f"t_{name}"


def tables(conn: dict[str, Any]) -> list[dict[str, Any]]:
    """Every table the app can see: its name to fetch it by, its display name, and whether it is
    one of the organisation's own (custom) tables. Read from the environment's metadata."""
    rest = extract.Rest(conn)
    url = conn["base_url"].rstrip("/") + "/EntityDefinitions"
    params = {
        "$select": "EntitySetName,LogicalName,DisplayName,IsCustomEntity,Description",
        "$filter": "IsPrivate eq false",
    }
    _status, _headers, data = rest.call(url, "GET", params, None)
    found = []
    for item in (data or {}).get("value", []):
        entity_set = item.get("EntitySetName")
        if not entity_set:
            continue
        label = ((item.get("DisplayName") or {}).get("UserLocalizedLabel") or {}).get("Label")
        found.append(
            {
                "entity_set": entity_set,
                "logical_name": item.get("LogicalName"),
                "label": label or item.get("LogicalName") or entity_set,
                "custom": bool(item.get("IsCustomEntity")),
                "table": table_name(entity_set),
            }
        )
    return sorted(found, key=lambda t: (not t["custom"], str(t["label"]).lower()))


def sample(conn: dict[str, Any], entity_set: str, rows: int = 50) -> list[dict[str, Any]]:
    """The first rows of a table, to make its raw table from (their columns and types)."""
    rest = extract.Rest(with_labels(conn))
    url = conn["base_url"].rstrip("/") + f"/{entity_set}"
    _status, _headers, data = rest.call(url, "GET", {"$top": rows}, None)
    return extract.records_at(data, "value") if data else []


def explain(message: str) -> str:
    """What a failed request means, in Dynamics' terms."""
    text = str(message)
    if "signing in at" in text:
        if "AADSTS7000215" in text or "invalid_client" in text or "secret" in text.lower():
            return "Microsoft did not accept the client secret: check it is the secret's value, not its id. " + text
        if "AADSTS700016" in text:
            return "Microsoft does not know that client id in this tenant: check both. " + text
        if "AADSTS90002" in text or "tenant" in text.lower():
            return "Microsoft does not know that tenant: check the tenant id. " + text
        return text
    if ": 403 " in text:
        return (
            "Signed in, but Dynamics refused: the app is not an application user of this environment, or its "
            "security role cannot read this. Add it in the Power Platform admin center (Environments → this "
            "environment → Settings → Users + permissions → Application users). " + text
        )
    if ": 401 " in text:
        return (
            "Dynamics did not accept the sign-in: check the URL is the environment the app was given access to. " + text
        )
    if "is not set" in text:
        return "The client id or secret is missing: enter them above. " + text
    return text


__all__ = [
    "explain",
    "DynamicsError",
    "connection",
    "endpoint",
    "host",
    "is_dynamics",
    "sample",
    "settings_of",
    "table_name",
    "tables",
    "variables",
]
