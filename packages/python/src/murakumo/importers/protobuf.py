"""Protobuf: read a .proto file, and make each message type a raw table.

    murakumo import protobuf events/orders.proto --source events
    murakumo import protobuf events/orders.proto --source events --message Order --message Refund

Each field becomes a column, its type from the protobuf type: integers and floats as numbers,
``google.protobuf.Timestamp`` as a timestamp, enums as text, and nested messages, maps and
repeated messages as JSON (repeated scalars as arrays). The extract code decodes the messages
with the protobuf library and lands them as JSON lines, one record per message, keeping the
fields' names as the .proto file writes them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from murakumo.importers.base import SourceImport, safe_name
from murakumo.ir.model import Attribute, Entity, SourceSystem, SourceType

SCALARS = {
    "double": "double",
    "float": "float",
    "int32": "integer",
    "sint32": "integer",
    "sfixed32": "integer",
    "uint32": "bigint",
    "fixed32": "bigint",
    "int64": "bigint",
    "sint64": "bigint",
    "sfixed64": "bigint",
    "uint64": "decimal(20,0)",
    "fixed64": "decimal(20,0)",
    "bool": "boolean",
    "string": "string",
    "bytes": "binary",
}
WELL_KNOWN = {
    "google.protobuf.Timestamp": "timestamp_tz",
    "google.protobuf.Duration": "string",
    "google.protobuf.StringValue": "string",
    "google.protobuf.Int64Value": "bigint",
    "google.protobuf.Int32Value": "integer",
    "google.protobuf.BoolValue": "boolean",
    "google.protobuf.DoubleValue": "double",
    "google.protobuf.Struct": "json",
}


@dataclass
class ProtoField:
    name: str
    type: str
    repeated: bool = False
    comment: str | None = None


@dataclass
class ProtoMessage:
    name: str  # as written, Outer.Inner for nested ones
    fields: list[ProtoField] = field(default_factory=list)
    comment: str | None = None


_TOKEN = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"])*"|[A-Za-z_][\w.]*|\d+|[{}<>;=,\[\]()]|\S', re.S)


def parse(text: str) -> dict[str, ProtoMessage]:
    """The message types in a .proto file, by name (nested ones as Outer.Inner), and their enums
    folded into text."""
    # A comment after a field, on its line, describes that field: put it before the field instead.
    text = re.sub(r"^([ \t]*)([^/\n]*;)[ \t]*//[ \t]*(.*)$", r"\1// \3\n\1\2", text, flags=re.M)
    tokens = _TOKEN.findall(text)
    messages: dict[str, ProtoMessage] = {}
    enums: set[str] = set()
    stack: list[str | None] = []  # the open blocks: a message's name, "" for others, None for a oneof
    comment: str | None = None
    i = 0

    def scope() -> str:
        return ".".join(s for s in stack if s)

    def in_message() -> str | None:
        """The message whose fields are being read, or None outside one (in a service, say)."""
        for entry in reversed(stack):
            if entry is None:
                continue
            return scope() if entry else None
        return None

    while i < len(tokens):
        token = tokens[i]
        if token.startswith("//") or token.startswith("/*"):
            comment = token.lstrip("/*").rstrip("*/").strip()
            i += 1
            continue
        if token in {"message", "enum"} and i + 2 < len(tokens) and tokens[i + 2] == "{":
            name = tokens[i + 1]
            full = f"{scope()}.{name}" if scope() else name
            if token == "message":
                messages[full] = ProtoMessage(full, comment=comment)
                stack.append(name)
            else:
                enums.update({full, name})
                stack.append("")
                # skip the enum's body
                depth, i = 1, i + 3
                while i < len(tokens) and depth:
                    depth += {"{": 1, "}": -1}.get(tokens[i], 0)
                    i += 1
                stack.pop()
                comment = None
                continue
            i += 3
            comment = None
            continue
        if token in {"oneof", "service", "extend"} and i + 2 < len(tokens) and tokens[i + 2] == "{":
            stack.append(None if token == "oneof" else "")
            i += 3
            continue
        if token == "{":
            stack.append("")
            i += 1
            continue
        if token == "}":
            if stack:
                stack.pop()
            i += 1
            continue
        current = in_message()
        if current in messages and token not in {"option", "reserved", "extensions", ";"}:
            # [repeated|optional] type name = number [options];   or   map<key, value> name = number;
            repeated = token == "repeated"
            if token in {"repeated", "optional", "required"}:
                i += 1
            kind = tokens[i]
            if kind == "map":
                close = tokens.index(">", i)
                kind, i = "map", close + 1
            else:
                i += 1
            if i + 1 < len(tokens) and tokens[i + 1] == "=":
                messages[current].fields.append(ProtoField(tokens[i], kind, repeated, comment))
            while i < len(tokens) and tokens[i] != ";":
                i += 1
            i += 1
            comment = None
            continue
        if token in {"option", "reserved", "extensions", "syntax", "package", "import"}:
            while i < len(tokens) and tokens[i] != ";":
                i += 1
        i += 1
        comment = None
    for message in messages.values():
        for f in message.fields:
            if f.type in enums or f.type.split(".")[-1] in {e.split(".")[-1] for e in enums}:
                f.type = "enum"
    return messages


def logical(f: ProtoField, messages: dict[str, ProtoMessage]) -> str:
    if f.type == "map":
        return "json"
    base = SCALARS.get(f.type) or WELL_KNOWN.get(f.type.lstrip(".")) or ("string" if f.type == "enum" else "json")
    if f.repeated:
        return f"array<{base}>" if base not in {"json"} else "json"
    return base


def import_proto(location: str, source: str, only: list[str] | None = None) -> SourceImport:
    """A raw table per message type (or the ones named), for a protobuf source."""
    path = Path(location)
    if not path.is_file():
        raise ValueError(f"no .proto file at {location}")
    messages = parse(path.read_text(encoding="utf-8"))
    if not messages:
        raise ValueError(f"{location} has no message types")
    wanted = only or [name for name in messages if "." not in name]
    result = SourceImport(source=SourceSystem(name=source, type=SourceType.STREAM), partial=bool(only))
    for name in wanted:
        message = messages.get(name) or next((m for k, m in messages.items() if k.split(".")[-1] == name), None)
        if message is None:
            raise ValueError(f"{location} has no message {name}; it has {', '.join(messages)}")
        attributes = [
            Attribute(name=safe_name(f.name), type=logical(f, messages), nullable=True, description=f.comment)
            for f in message.fields
        ]
        result.entities.append(
            Entity(
                name=safe_name(re.sub(r"(?<!^)(?=[A-Z])", "_", message.name.split(".")[-1]).lower(), "table"),
                source=source,
                description=message.comment,
                attributes=attributes,
                properties={"proto": str(path), "message": message.name},
            )
        )
    return result


__all__ = ["ProtoField", "ProtoMessage", "import_proto", "logical", "parse"]
