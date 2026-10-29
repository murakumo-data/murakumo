"""Pipeline docs: what the project builds, from where, when, and how each column is made.

``generated/docs/index.html`` is one page that needs nothing else (no internet, no server): open
it in a browser. ``generated/docs/<entity>.md`` has the same per table, for reading in the
repository. Everything comes from the model and murakumo.yaml, with no dates in it, so the docs
change only when the project does and `murakumo generate --check` stays reliable.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from html import escape
from typing import Any

from murakumo.generate.plan import Plan
from murakumo.ir.model import Entity, Layer
from murakumo.lineage import build_lineage
from murakumo.schedules import ScheduleError, resolve
from murakumo.sources import list_sources

LAYERS = [layer.value for layer in Layer]
NOTHING = '<span class="muted">nothing</span>'


@dataclass
class Page:
    """Everything the docs say about one entity."""

    entity: Entity
    schedules: list[str] = field(default_factory=list)  # "nightly: every day at 06:00 UTC"
    models: list[str] = field(default_factory=list)  # dbt models built for it
    upstream: list[str] = field(default_factory=list)  # tables it is built from
    downstream: list[str] = field(default_factory=list)  # tables built from it
    columns: list[dict[str, Any]] = field(default_factory=list)
    relationships: list[str] = field(default_factory=list)


def _pages(project, plan: Plan) -> list[Page]:
    lineage = build_lineage(project)
    timezone = project.manifest.orchestration.timezone
    try:
        refreshed: dict[str, list[str]] = defaultdict(list)
        from murakumo.cron import describe

        for entry in resolve(project):
            for entity in entry.entities:
                refreshed[entity.name].append(f"{entry.schedule.name}: {describe(entry.schedule.cron, timezone)}")
    except ScheduleError:
        refreshed = defaultdict(list)
    models: dict[str, list[str]] = defaultdict(list)
    for model in plan.models:
        models[model.entity.name].append(model.name)
    for snapshot in plan.snapshots:
        if snapshot.entity is not None:
            models[snapshot.entity.name].append(snapshot.name)

    pages = []
    for entity in sorted(project.model.entities, key=lambda e: (LAYERS.index(e.layer.value), e.name.lower())):
        page = Page(entity, refreshed.get(entity.name, []), models.get(entity.name, []))
        up, down = set(), set()
        mappings = {m.target.lower(): m for m in entity.mappings}
        for attribute in entity.attributes:
            ref = (entity.name, attribute.name)
            sources = [f"{e}.{c}" for e, c in lineage.sources_of(*ref)]
            users = [f"{f.target[0]}.{f.target[1]}" for f in lineage.downstream.get(ref, [])]
            up.update(s.split(".")[0] for s in sources)
            down.update(u.split(".")[0] for u in users)
            mapping = mappings.get(attribute.name.lower())
            tests = [t.type.value for t in attribute.tests]
            if attribute.key and attribute.key.value == "primary":
                tests = sorted({*tests, "unique", "not_null"})
            page.columns.append(
                {
                    "name": attribute.name,
                    "type": attribute.type,
                    "nullable": attribute.nullable,
                    "key": attribute.key.value if attribute.key else None,
                    "description": attribute.description,
                    "pii": attribute.pii,
                    "classification": attribute.classification.value if attribute.classification else None,
                    "unit": attribute.unit,
                    "accepted": sorted(attribute.accepted_values) if attribute.accepted_values else [],
                    "tests": tests,
                    "sources": sources,
                    "expression": mapping.expression if mapping else None,
                    "used_by": users,
                }
            )
        page.upstream = sorted(up - {entity.name})
        page.downstream = sorted(down - {entity.name})
        for rel in project.model.relationships:
            if entity.name.lower() in (rel.from_.lower(), rel.to.lower()):
                keys = ", ".join(f"{rel.from_}.{k.from_} = {rel.to}.{k.to}" for k in rel.keys)
                role = f" ({rel.role})" if rel.role else ""
                page.relationships.append(f"{rel.from_} → {rel.to}{role}: {rel.cardinality.value}, {keys}")
        pages.append(page)
    return pages


def _anchor(name: str) -> str:
    return "t-" + name.lower()


# HTML -----------------------------------------------------------------------------------------

_CSS = """
:root{--bg:#f7f7f4;--surface:#fff;--text:#1b1b19;--muted:#6b6b66;--border:#dcdcd5;--accent:#2459d6;
--chip:#eef1f8;--raw:#8a8a83;--staging:#128a8a;--intermediate:#7b52c7;--marts:#2459d6;--semantic:#b7791f}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--surface:#1d1d1b;--text:#ecece8;--muted:#a3a39c;
--border:#34342f;--accent:#8fb2ff;--chip:#262a35}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.layout{display:grid;grid-template-columns:260px minmax(0,1fr);min-height:100vh}
nav{position:sticky;top:0;height:100vh;overflow-y:auto;padding:16px;border-right:1px solid var(--border);
background:var(--surface)}
nav input{width:100%;padding:6px 8px;margin:8px 0 12px;border:1px solid var(--border);border-radius:6px;
background:var(--bg);color:var(--text)}
nav h2{margin:12px 0 4px;font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
nav ul{list-style:none;margin:0;padding:0}nav li a{display:block;padding:2px 6px;border-radius:4px;color:var(--text)}
nav li a:hover{background:var(--chip);text-decoration:none}
main{padding:24px 32px;max-width:1100px}h1{margin:0 0 4px}section{margin:28px 0}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px 18px;margin:14px 0}
.card h3{margin:0 0 6px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.layer{display:inline-block;width:10px;height:10px;border-radius:50%}
.chip{display:inline-block;padding:1px 8px;border-radius:999px;background:var(--chip);font-size:12px;
margin:2px 4px 2px 0}
.muted{color:var(--muted)}table{width:100%;border-collapse:collapse;margin-top:8px}
th,td{text-align:left;vertical-align:top;padding:5px 8px;border-bottom:1px solid var(--border)}
th{color:var(--muted);font-weight:500;font-size:12px}.flow{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.meta{display:grid;grid-template-columns:120px minmax(0,1fr);gap:2px 12px;margin:8px 0}
.meta dt{color:var(--muted)}.meta dd{margin:0}
@media (max-width:760px){.layout{grid-template-columns:1fr}nav{position:static;height:auto}main{padding:16px}}
"""

_SEARCH = """
document.querySelector('#find').addEventListener('input', (e) => {
  const text = e.target.value.toLowerCase()
  document.querySelectorAll('[data-name]').forEach((el) => {
    el.style.display = el.dataset.name.includes(text) ? '' : 'none'
  })
})
"""


def _dot(layer: str) -> str:
    return f'<span class="layer" style="background:var(--{layer})" title="{layer}"></span>'


def _link(name: str) -> str:
    return f'<a href="#{_anchor(name)}"><code>{escape(name)}</code></a>'


def _html(project, plan: Plan, pages: list[Page]) -> str:
    manifest = project.manifest
    name = manifest.project.name
    by_layer: dict[str, list[Page]] = defaultdict(list)
    for page in pages:
        by_layer[page.entity.layer.value].append(page)

    nav = ["<nav><strong>" + escape(name) + "</strong>", '<input id="find" type="search" placeholder="Find a table">']
    nav.append('<ul><li><a href="#overview">Overview</a></li><li><a href="#schedules">Schedules</a></li>'
               '<li><a href="#sources">Sources</a></li></ul>')  # fmt: skip
    for layer in LAYERS:
        if by_layer.get(layer):
            nav.append(f"<h2>{layer}</h2><ul>")
            nav += [
                f'<li data-name="{escape(p.entity.name.lower())}"><a href="#{_anchor(p.entity.name)}">'
                f"{escape(p.entity.name)}</a></li>"
                for p in by_layer[layer]
            ]
            nav.append("</ul>")
    nav.append("</nav>")

    body = [f'<section id="overview"><h1>{escape(name)}</h1>']
    if manifest.project.description:
        body.append(f"<p>{escape(manifest.project.description)}</p>")
    counts = ", ".join(f"{len(by_layer[layer])} {layer}" for layer in LAYERS if by_layer.get(layer))
    body.append(
        f'<p class="muted">{len(pages)} tables ({counts}) on {plan.target.warehouse.value}, '
        f"run by {manifest.generation.orchestrator.value}, modelled as {manifest.modelling.pattern.value}.</p>"
    )
    if manifest.project.domains:
        body.append("<h2>Domains</h2><table><tr><th>Domain</th><th>Owner</th><th>Tables</th></tr>")
        for domain in manifest.project.domains:
            members = [p.entity.name for p in pages if (p.entity.domain or "").lower() == domain.name.lower()]
            body.append(
                f"<tr><td><strong>{escape(domain.name)}</strong>"
                + (f'<div class="muted">{escape(domain.description)}</div>' if domain.description else "")
                + f"</td><td>{escape(domain.owner or '')}</td><td>{' '.join(_link(m) for m in members)}</td></tr>"
            )
        body.append("</table>")
    body.append("</section>")

    body.append('<section id="schedules"><h2>Schedules</h2>')
    try:
        from murakumo.cron import describe

        entries = resolve(project)
        body.append("<table><tr><th>Schedule</th><th>When</th><th>Refreshes</th></tr>")
        for entry in entries:
            when = describe(entry.schedule.cron, manifest.orchestration.timezone)
            body.append(
                f"<tr><td><strong>{escape(entry.schedule.name)}</strong>"
                + (
                    f'<div class="muted">{escape(entry.schedule.description)}</div>'
                    if entry.schedule.description
                    else ""
                )
                + f'</td><td>{escape(when)}<div class="mono muted">{escape(entry.schedule.cron)}</div></td>'
                + f"<td>{' '.join(_link(e.name) for e in entry.entities) or NOTHING}</td></tr>"
            )
        body.append("</table>")
        if entries and entries[0].implicit:
            body.append('<p class="muted">No schedules are set in murakumo.yaml, so everything runs once a day.</p>')
    except ScheduleError as exc:
        body.append(f"<p>Schedules cannot be read: {escape(str(exc))}</p>")
    body.append("</section>")

    body.append(
        '<section id="sources"><h2>Sources</h2><table><tr><th>Source</th><th>Read from</th><th>Tables</th></tr>'
    )
    freshness = {g.name: g.freshness for g in plan.sources}
    for source in list_sources(project):
        tables = [p.entity.name for p in pages if (p.entity.source or "").lower() == source["name"].lower()]
        fresh = freshness.get(source["name"].lower()) or freshness.get(source["name"])
        note = ""
        if fresh:
            limits = [f"{k.split('_')[0]} after {v['count']} {v['period']}s" for k, v in fresh.items()]
            note = f'<div class="muted">Freshness: {escape(", ".join(limits))}</div>'
        body.append(
            f"<tr><td><strong>{escape(source['name'])}</strong>{note}</td>"
            f"<td><code>{escape(source['from'] or '')}</code></td><td>{' '.join(_link(t) for t in tables)}</td></tr>"
        )
    body.append("</table></section>")

    for layer in LAYERS:
        if not by_layer.get(layer):
            continue
        body.append(f"<section><h2>{layer.capitalize()}</h2>")
        for page in by_layer[layer]:
            body.append(_entity_card(page))
        body.append("</section>")

    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape(name)} pipeline docs</title>\n"
        "<!-- Generated by Murakumo. Do not edit: change the model, then run `murakumo generate`. -->\n"
        f'<style>{_CSS}</style>\n</head>\n<body>\n<div class="layout">\n'
        + "\n".join(nav)
        + "\n<main>\n"
        + "\n".join(body)
        + f"\n</main>\n</div>\n<script>{_SEARCH}</script>\n</body>\n</html>\n"
    )


def _entity_card(page: Page) -> str:
    entity = page.entity
    out = [
        f'<div class="card" id="{_anchor(entity.name)}" data-name="{escape(entity.name.lower())}">',
        f"<h3>{_dot(entity.layer.value)}<code>{escape(entity.name)}</code>"
        f'<span class="chip">{entity.layer.value}</span>'
        + (f'<span class="chip">{entity.kind.value}</span>' if entity.kind.value != "table" else "")
        + (f'<span class="chip">{escape(entity.domain)}</span>' if entity.domain else "")
        + "</h3>",
    ]
    if entity.description:
        out.append(f"<p>{escape(entity.description)}</p>")
    meta = []
    if entity.source:
        meta.append(("Source", escape(entity.source)))
    if entity.owner:
        meta.append(("Owner", escape(entity.owner)))
    if entity.grain:
        meta.append(("Grain", ", ".join(f"<code>{escape(g)}</code>" for g in entity.grain)))
    if entity.history.value != "none":
        meta.append(("History", entity.history.value))
    if entity.load.value != "full":
        watermark = f" on <code>{escape(entity.watermark)}</code>" if entity.watermark else ""
        meta.append(("Load", f"{entity.load.value}{watermark}"))
    meta.append(("Refreshed", escape("; ".join(page.schedules)) or '<span class="muted">by no schedule</span>'))
    if page.models:
        meta.append(("dbt", ", ".join(f"<code>{escape(m)}</code>" for m in page.models)))
    out.append('<dl class="meta">' + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in meta) + "</dl>")
    if page.upstream or page.downstream:
        up = " ".join(_link(t) for t in page.upstream) or '<span class="muted">sources</span>'
        down = " ".join(_link(t) for t in page.downstream) or '<span class="muted">nothing yet</span>'
        out.append(f'<div class="flow">{up} → <strong><code>{escape(entity.name)}</code></strong> → {down}</div>')
    out.append("<table><tr><th>Column</th><th>Type</th><th>About</th><th>Comes from</th><th>Used by</th></tr>")
    for column in page.columns:
        flags = []
        if column["key"]:
            flags.append(column["key"] + " key")
        if not column["nullable"]:
            flags.append("not null")
        if column["pii"]:
            flags.append("PII")
        if column["classification"]:
            flags.append(column["classification"])
        about = escape(column["description"] or "")
        if flags:
            about += "".join(f' <span class="chip">{escape(f)}</span>' for f in flags)
        if column["tests"]:
            about += f'<div class="muted">Tests: {escape(", ".join(column["tests"]))}</div>'
        if column["accepted"]:
            about += f'<div class="muted">Values: {escape(", ".join(column["accepted"]))}</div>'
        origin = "<br>".join(f"<code>{escape(s)}</code>" for s in column["sources"])
        if column["expression"]:
            origin += f'<div class="mono muted">{escape(column["expression"])}</div>'
        used = "<br>".join(f"<code>{escape(u)}</code>" for u in column["used_by"])
        unit = f" ({escape(column['unit'])})" if column["unit"] else ""
        out.append(
            f"<tr><td><code>{escape(column['name'])}</code></td><td><code>{escape(column['type'])}</code>{unit}</td>"
            f"<td>{about}</td><td>{origin}</td><td>{used}</td></tr>"
        )
    out.append("</table>")
    if page.relationships:
        out.append(
            '<p class="muted">Relationships:</p><ul>'
            + "".join(f"<li class=mono>{escape(r)}</li>" for r in page.relationships)
            + "</ul>"
        )
    out.append("</div>")
    return "\n".join(out)


# Markdown -------------------------------------------------------------------------------------


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _markdown(page: Page) -> str:
    entity = page.entity
    lines = [f"# {entity.name}", "", "_Generated by Murakumo. Do not edit: change the model, then run "
             "`murakumo generate`._", ""]  # fmt: skip
    if entity.description:
        lines += [entity.description, ""]
    facts = [f"**Layer:** {entity.layer.value}", f"**Kind:** {entity.kind.value}"]
    if entity.domain:
        facts.append(f"**Domain:** {entity.domain}")
    if entity.source:
        facts.append(f"**Source:** {entity.source}")
    facts.append("**Refreshed:** " + ("; ".join(page.schedules) or "by no schedule"))
    lines += [" · ".join(facts), ""]
    if page.upstream or page.downstream:
        up = ", ".join(f"[{t}]({t}.md)" for t in page.upstream) or "sources"
        down = ", ".join(f"[{t}]({t}.md)" for t in page.downstream) or "nothing yet"
        lines += [f"{up} → **{entity.name}** → {down}", ""]
    lines += ["| Column | Type | About | Comes from |", "| --- | --- | --- | --- |"]
    for column in page.columns:
        about = column["description"] or ""
        flags = [f for f in (column["key"] and f"{column['key']} key", "PII" if column["pii"] else None) if f]
        if flags:
            about = (about + " " if about else "") + f"({', '.join(flags)})"
        origin = ", ".join(f"`{s}`" for s in column["sources"])
        if column["expression"]:
            origin += f" as `{column['expression']}`"
        lines.append(f"| `{column['name']}` | `{column['type']}` | {_cell(about)} | {_cell(origin)} |")
    if page.relationships:
        lines += ["", "**Relationships**", ""] + [f"- {r}" for r in page.relationships]
    return "\n".join(lines) + "\n"


def _index(project, pages: list[Page]) -> str:
    lines = [f"# {project.manifest.project.name}: pipeline docs", "",
             "Open `index.html` for the full page. One file per table:", ""]  # fmt: skip
    for layer in LAYERS:
        names = [p.entity.name for p in pages if p.entity.layer.value == layer]
        if names:
            lines += [f"**{layer}:** " + ", ".join(f"[{n}]({n}.md)" for n in names), ""]
    return "\n".join(lines)


def render(project, plan: Plan) -> dict[str, str]:
    pages = _pages(project, plan)
    files = {"index.html": _html(project, plan, pages), "README.md": _index(project, pages)}
    for page in pages:
        files[f"{page.entity.name}.md"] = _markdown(page)
    return files


def html(project, plan: Plan) -> str:
    return _html(project, plan, _pages(project, plan))


__all__ = ["html", "render"]
