"""The canvas API: read and change a project's model over HTTP, through the same edits as the CLI.

Every request loads the project from disk, so changes made in an editor or with the CLI show up
straight away, and every change is saved to the YAML files before the response is sent. The
response to a change is the whole new state, validated, so the canvas never has to guess.

Two people (or a person and their editor) can change the same files, so each state carries a
``revision``: a fingerprint of the model files. A change sent with an ``X-Murakumo-Revision``
header that no longer matches is refused with 409 and the current state, instead of overwriting
what someone else saved.

The server only listens on 127.0.0.1 and only answers requests addressed to localhost, so a web
page in another tab cannot reach it by DNS tricks; changes need a JSON body, which browsers will
not send across sites without the server's consent.
"""

from __future__ import annotations

import difflib
import hashlib
import re
import threading
from collections.abc import Callable
from contextvars import ContextVar
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from murakumo import __version__, edit
from murakumo import history as history_module
from murakumo import schedules as schedules_module
from murakumo import sources as sources_module
from murakumo.diagnostics import Diagnostic, Level
from murakumo.expressions import check_expression, preview_expression
from murakumo.generate import GENERATED_DIR, INDEX_FILE, compare, generate, write
from murakumo.ir.model import Cardinality, Classification, EntityKind, Layer
from murakumo.ir.types import TypeName
from murakumo.lineage import resolve_ref, trace
from murakumo.manifest import MANIFEST_FILE, Warehouse
from murakumo.migrate import MIGRATIONS_DIR, Pending, plan_migration, write_migration
from murakumo.project import MODEL_DIR, Project, load_canvas, load_layout, load_project, save_layout, save_model
from murakumo.validate import validate_project

REVISION_HEADER = "X-Murakumo-Revision"
PREVIEW_HEADER = "X-Murakumo-Preview"  # on an edit: say what it would reach, without saving it
PREVIEW: ContextVar[bool] = ContextVar("preview", default=False)
LOCAL_HOSTS = ["localhost", "127.0.0.1"]
UPLOADS_DIR = "data"
UPLOAD_NAME = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
UPLOAD_TYPES = {".csv", ".parquet", ".json", ".jsonl", ".xlsx", ".xls", ".tsv"}
UPLOAD_LIMIT = 2_000_000_000  # 2 GB: a month of NYC taxi trips is 50 MB

Revision = Annotated[str | None, Header(alias=REVISION_HEADER)]
Changes = Annotated[dict[str, Any], Body()]


# Request bodies ---------------------------------------------------------------------------------


class Position(BaseModel):
    x: float
    y: float


class NewEntity(BaseModel):
    name: str
    layer: Layer = Layer.RAW
    kind: EntityKind = EntityKind.TABLE
    source: str | None = None
    domain: str | None = None
    description: str | None = None
    position: Position | None = None


class NewColumn(BaseModel):
    name: str
    type: str
    primary_key: bool = False
    nullable: bool | None = None
    description: str | None = None
    pii: bool = False
    classification: Classification | None = None


class JoinPair(BaseModel):
    from_: str = Field(alias="from")
    to: str


class NewRelationship(BaseModel):
    from_: str = Field(alias="from")
    to: str
    keys: list[JoinPair]
    cardinality: Cardinality = Cardinality.MANY_TO_ONE
    name: str | None = None
    optional: bool = False
    role: str | None = None


class Layout(BaseModel):
    positions: dict[UUID, Position]


class SettingsBody(BaseModel):
    """What `murakumo config` and murakumo.yaml's switches set; only the fields given change."""

    warehouse: Warehouse | None = None
    orchestrator: str | None = None
    pattern: str | None = None
    python_engine: str | None = Field(default=None, pattern="^(pyspark|polars|none)$")
    plain_sql: bool | None = None
    run_log: bool | None = None
    raw_text: bool | None = None
    base_branch: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[\w./-]+$")
    personal_schemas: bool | None = None
    personal_database: str | None = Field(default=None, max_length=255, pattern=r"^[\w.$-]*$")
    environments: list[EnvironmentBody] | None = None


class EnvironmentBody(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    database: str | None = Field(default=None, max_length=255, pattern=r"^[\w.$/-]*$")
    branch: str | None = Field(default=None, max_length=100, pattern=r"^[\w./-]*$")


class BranchBody(BaseModel):
    kind: str = Field(pattern="^(feature|fix|chore|hotfix)$")
    name: str = Field(min_length=1, max_length=80)


class JobBody(BaseModel):
    kind: str
    options: dict[str, Any] = Field(default_factory=dict)


class CopyColumns(BaseModel):
    columns: list[str] = Field(min_length=1)  # entity.column


class BulkChange(BaseModel):
    ids: list[UUID] = Field(min_length=1)
    changes: dict[str, Any]


class BulkRemove(BaseModel):
    ids: list[UUID] = Field(min_length=1)


class Label(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    colour: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    description: str | None = None


class Note(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    text: str = ""
    x: int = 0
    y: int = 0
    width: int = Field(default=220, ge=80, le=1200)
    colour: str | None = None
    diagram: str | None = None  # the diagram it was written on; none: shown on "All tables"


class Diagram(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    entities: list[str] = Field(default_factory=list)


class CanvasSettings(BaseModel):
    """What the canvas draws besides the model: given keys replace what is saved, others stay."""

    label: str | None = None  # what undo calls this change
    collapse_over: int | None = Field(default=None, ge=0, le=500)
    collapsed: dict[str, bool] | None = None
    labels: list[Label] | None = None
    colours: dict[str, str] | None = None
    notes: list[Note] | None = None
    diagrams: list[Diagram] | None = None


class MappingBody(BaseModel):
    sources: list[str] = Field(default_factory=list)
    expression: str | None = None


class MigrationBody(BaseModel):
    name: str | None = None


class ColumnOrder(BaseModel):
    columns: list[UUID]


class ExpressionBody(BaseModel):
    expression: str
    sources: list[str] = Field(default_factory=list)
    entity: str | None = None  # the entity whose column it calculates


class CalculatedColumn(BaseModel):
    name: str
    expression: str
    type: str | None = None


class NewSource(BaseModel):
    name: str
    kind: str  # database, snowflake, csv, excel, file or openapi
    location: str = ""  # an environment variable's name for a database; a path or URL otherwise


class SourceImportBody(BaseModel):
    tables: list[str] | None = None  # schema.table or table; None imports the whole source
    prune: bool = False


class NewSchedule(BaseModel):
    name: str
    cron: str
    select: list[str] = Field(default_factory=list)
    description: str | None = None


class TimezoneBody(BaseModel):
    timezone: str


class CIBody(BaseModel):
    provider: str = "github"
    deploy: bool = False
    preview: bool = False
    force: bool = False


class CommitBody(BaseModel):
    message: str


class NewDomain(BaseModel):
    name: str
    owner: str | None = None
    description: str | None = None


# State ------------------------------------------------------------------------------------------


def _fingerprint(files: list[Path], root: Path) -> str:
    digest = hashlib.sha1()
    for path in files:
        if path.is_file():
            stat = path.stat()
            digest.update(f"{path.relative_to(root)}:{stat.st_size}:{stat.st_mtime_ns};".encode())
    return digest.hexdigest()[:16]


def revision(root: Path) -> str:
    """A fingerprint of the manifest and every model file: path, size and modification time."""
    return _fingerprint([root / MANIFEST_FILE, *sorted((root / MODEL_DIR).rglob("*"))], root)


def generated_revision(root: Path) -> str:
    """A fingerprint of what was last generated and migrated, to notice `murakumo generate` runs."""
    files = [root / GENERATED_DIR / INDEX_FILE, *sorted((root / MIGRATIONS_DIR).rglob("*"))]
    return _fingerprint(files, root)


def _diagnostic(d: Diagnostic, project: Project | None, root: Path) -> dict[str, Any]:
    entity_id = None
    if project is not None and d.file is not None:
        entity_id = next((str(i) for i, path in project.origins.items() if path == d.file), None)
    file = d.file.relative_to(root) if d.file is not None and d.file.is_relative_to(root) else d.file
    return {
        "level": d.level.value,
        "code": d.code,
        "message": d.message,
        "file": str(file) if file is not None else None,
        "where": d.where,
        "entity_id": entity_id,
        "column_id": _column_of(d, project, entity_id),
    }


_COLUMN_WHERE = re.compile(r"^(?:attributes|mappings)\[([^\]]+)\]")


def _column_of(d: Diagnostic, project: Project | None, entity_id: str | None) -> str | None:
    """The column a problem is about, from where it was found (``attributes[name]``, ``mappings[name]``),
    so the canvas can mark the column itself."""
    match = _COLUMN_WHERE.match(d.where or "")
    if project is None or entity_id is None or match is None:
        return None
    entity = next((e for e in project.model.entities if str(e.id) == entity_id), None)
    column = entity and next((a for a in entity.attributes if a.name.lower() == match.group(1).lower()), None)
    return str(column.id) if column else None


HISTORIES: dict[Path, history_module.History] = {}  # one undo history per project the server edits


def state(root: Path) -> dict[str, Any]:
    """Everything the canvas draws: the model, positions, diagnostics and the choices for its forms."""
    result = load_project(root)
    project = result.project
    diagnostics = list(result.diagnostics)
    editable = project is not None and not any(d.level is Level.ERROR for d in diagnostics)
    if project is not None:
        diagnostics += validate_project(project)
    layout = load_layout(root) if project is not None else {}
    sources = sorted(
        {s.name for s in project.model.sources} | {s.name for s in project.manifest.sources} if project else set(),
        key=str.lower,
    )
    return {
        "version": __version__,
        "revision": revision(root),
        "project": project.manifest.project.name if project else root.name,
        "editable": editable,
        "model": project.model.model_dump(mode="json", by_alias=True) if project else None,
        "layout": {str(k): {"x": x, "y": y} for k, (x, y) in layout.items()},
        "canvas": load_canvas(root) if project is not None else {},
        "sources": sources_module.list_sources(project) if project else [],
        "orchestration": schedules_module.summary(project) if project else None,
        "domains": [
            {**d.model_dump(), "entities": len(edit.domain_usage(project, d.name))}
            for d in (project.manifest.project.domains if project else [])
        ],
        "diagnostics": [_diagnostic(d, project, root) for d in diagnostics],
        "history": HISTORIES[root].summary() if root in HISTORIES else {"undo": None, "redo": None},
        "environments": {
            "personal": project.manifest.collaboration.personal_environment if project else "personal",
            "shared": project.manifest.targets.names if project else [],
        },
        "choices": {
            "sources": sources,
            "layers": [v.value for v in Layer],
            "kinds": [v.value for v in EntityKind],
            "cardinalities": [v.value for v in Cardinality],
            "classifications": [v.value for v in Classification],
            "types": [v.value for v in TypeName],
        },
    }


# Generated code -------------------------------------------------------------------------------


def _blocked(root: Path) -> tuple[Project | None, str | None]:
    """The project, if it can be generated; otherwise why not."""
    result = load_project(root)
    project = result.project
    if project is None or any(d.level is Level.ERROR for d in result.diagnostics):
        return None, "Some model files did not load; fix them first."
    if any(d.level is Level.ERROR for d in validate_project(project)):
        return project, "The model has errors; fix them to see the generated code."
    return project, None


def _migration(pending: Pending) -> dict[str, Any] | None:
    if not pending.changes and not pending.baseline:
        return None
    return {
        "number": pending.number,
        "file": f"{MIGRATIONS_DIR}/{pending.warehouse.value}/{pending.file_name}",
        "baseline": pending.baseline,
        "changes": [{"summary": c.summary, "destructive": c.destructive} for c in pending.changes],
        "sql": pending.sql,
    }


def generated(root: Path, warehouse: Warehouse | None = None) -> dict[str, Any]:
    """What `murakumo generate` would write, compared with generated/, and the next migration."""
    project, blocked = _blocked(root)
    chosen = Warehouse(warehouse or (project.manifest.targets.warehouse.type if project else Warehouse.DUCKDB))
    summary: dict[str, Any] = {
        "warehouse": chosen.value,
        "revision": generated_revision(root),
        "blocked": blocked,
        "diagnostics": [],
        "files": [],
        "migration": None,
        "notes": [],
    }
    if project is None or blocked:
        return summary
    output = generate(project, chosen)
    changes = compare(output, root)
    status = {p: "added" for p in changes.added} | {p: "changed" for p in changes.changed}
    files = [
        {
            "path": path,
            "status": status.get(path, "unchanged"),
            "entities": [str(i) for i in output.owners.get(path, [])],
            "edited_by_hand": path in changes.edited_by_hand,
        }
        for path in output.files
    ]
    files += [{"path": p, "status": "removed", "entities": [], "edited_by_hand": False} for p in changes.removed]
    summary["files"] = sorted(files, key=lambda f: f["path"])
    summary["diagnostics"] = [_diagnostic(d, project, root) for d in output.diagnostics]
    pending = plan_migration(project, chosen)
    summary["migration"] = _migration(pending)
    summary["notes"] = pending.notes
    return summary


def _diff(before: str, after: str, path: str) -> str:
    lines = difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True), f"a/{path}", f"b/{path}", n=3
    )
    return "".join(lines)


# App --------------------------------------------------------------------------------------------


def canvas_files() -> Path | None:
    """The built canvas: packaged next to this module, or the repository's apps/canvas/dist."""
    here = Path(__file__).resolve().parent
    for folder in (here / "web", here.parents[3] / "apps" / "canvas" / "dist" if len(here.parents) > 3 else None):
        if folder is not None and (folder / "index.html").is_file():
            return folder
    return None


def create_app(root: Path, static: Path | None = None) -> FastAPI:
    root = root.resolve()
    lock = threading.Lock()  # one change at a time: load, edit, save
    history = HISTORIES.setdefault(root, history_module.History())
    app = FastAPI(
        title="Murakumo canvas API", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json"
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)

    @app.middleware("http")
    async def preview_header(request, call_next):
        token = PREVIEW.set(request.headers.get(PREVIEW_HEADER) == "1")
        try:
            return await call_next(request)
        finally:
            PREVIEW.reset(token)

    def change(
        action: Callable[[Project], Any], expected: str | None, prune: bool = False, previewable: bool = False
    ) -> dict[str, Any]:
        with lock:
            if expected and expected != revision(root):
                raise HTTPException(
                    409, {"message": "the model changed on disk; showing the latest", "state": state(root)}
                )
            result = load_project(root)
            project = result.project
            if project is None or any(d.level is Level.ERROR for d in result.diagnostics):
                raise HTTPException(
                    409, {"message": "fix the errors in the model files before editing", "state": state(root)}
                )
            before = history_module.take(root)
            old_model, old_manifest = project.model.model_copy(deep=True), project.manifest.model_copy(deep=True)
            try:
                placed = action(project)
            except edit.EditError as exc:
                raise HTTPException(400, {"message": str(exc)}) from None
            if previewable and PREVIEW.get():
                # Asked what the edit would reach: answer from memory, and save nothing.
                from murakumo.impact import report

                before = load_project(root).project.model
                return {"preview": report(project, before, project.model).as_dict()}
            positions = load_layout(root)
            if isinstance(placed, dict):  # an action that adds an entity may say where to draw it
                positions.update(placed)
            save_model(project, prune=prune)
            save_layout(project, positions)  # drops the positions of removed entities
            history.record(
                history_module.label(old_model, project.model, old_manifest, project.manifest),
                before,
                history_module.take(root),
            )
            return state(root)

    def entity_name(project: Project, entity_id: UUID) -> str:
        entity = next((e for e in project.model.entities if e.id == entity_id), None)
        if entity is None:
            raise edit.EditError(f"entity {entity_id} does not exist")
        return entity.name

    def column_name(project: Project, entity_id: UUID, column_id: UUID) -> str:
        entity = project.model.entity(entity_name(project, entity_id))
        column = next((a for a in entity.attributes if a.id == column_id), None)
        if column is None:
            raise edit.EditError(f"{entity.name} has no column {column_id}")
        return column.name

    @app.exception_handler(HTTPException)
    def _error(_request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        return JSONResponse(detail, status_code=exc.status_code)

    @app.get("/api/state")
    def get_state() -> dict[str, Any]:
        return state(root)

    @app.get("/api/revision")
    def get_revision() -> dict[str, str]:
        """Cheap to poll: the canvas reloads the state, or the generated code, when these change."""
        return {"revision": revision(root), "generated": generated_revision(root)}

    @app.get("/api/generated")
    def get_generated(warehouse: Warehouse | None = None) -> dict[str, Any]:
        return generated(root, warehouse)

    @app.get("/api/generated/file")
    def get_generated_file(path: str, warehouse: Warehouse | None = None) -> dict[str, Any]:
        """One file as it would be generated, and its diff against what is in generated/."""
        project, blocked = _blocked(root)
        if project is None or blocked:
            raise HTTPException(409, {"message": blocked})
        output = generate(project, warehouse)
        folder = (root / GENERATED_DIR).resolve()
        on_disk = (folder / path).resolve()
        if not on_disk.is_relative_to(folder):
            raise HTTPException(400, {"message": "not a generated file"})
        before = on_disk.read_text(encoding="utf-8") if on_disk.is_file() else ""
        after = output.files.get(path)
        if after is None and not before:
            raise HTTPException(404, {"message": f"{path} is not generated"})
        return {"path": path, "content": after, "diff": _diff(before, after or "", path)}

    @app.get("/api/entities/{entity_id}/code")
    def get_entity_code(entity_id: UUID, warehouse: Warehouse | None = None) -> dict[str, Any]:
        """The dbt SQL generated for one entity, and its table's DDL."""
        project, blocked = _blocked(root)
        if project is None or blocked:
            return {"blocked": blocked, "files": [], "ddl": None}
        output = generate(project, warehouse)
        changes = compare(output, root)
        status = {p: "added" for p in changes.added} | {p: "changed" for p in changes.changed}
        files = [
            {"path": path, "status": status.get(path, "unchanged"), "content": output.files[path]}
            for path, owners in output.owners.items()
            if entity_id in owners and path.endswith(".sql") and path.startswith("dbt/")
        ]
        return {"blocked": None, "files": files, "ddl": output.ddl.get(entity_id)}

    @app.post("/api/generate")
    def post_generate(warehouse: Warehouse | None = None) -> dict[str, Any]:
        """Write generated/, as `murakumo generate` does."""
        with lock:
            project, blocked = _blocked(root)
            if project is None or blocked:
                raise HTTPException(409, {"message": blocked})
            write(generate(project, warehouse), root)
            return generated(root, warehouse)

    @app.post("/api/migrations")
    def post_migration(body: MigrationBody, warehouse: Warehouse | None = None) -> dict[str, Any]:
        """Write the next migration, as `murakumo migrate` does."""
        with lock:
            project, blocked = _blocked(root)
            if project is None or blocked:
                raise HTTPException(409, {"message": blocked})
            pending = plan_migration(project, warehouse)
            if write_migration(project, pending, body.name) is None:
                raise HTTPException(409, {"message": "nothing has changed since the last migration"})
            return generated(root, warehouse)

    @app.put("/api/entities/{entity_id}/mappings/{column_id}")
    def put_mapping(entity_id: UUID, column_id: UUID, body: MappingBody, rev: Revision = None) -> dict[str, Any]:
        def action(project: Project) -> None:
            entity, column = entity_name(project, entity_id), column_name(project, entity_id, column_id)
            edit.set_mapping(project, entity, column, body.sources, body.expression)

        return change(action, rev)

    @app.post("/api/entities")
    def add_entity(body: NewEntity, rev: Revision = None) -> dict[str, Any]:
        def action(project: Project) -> dict[UUID, tuple[int, int]]:
            entity = edit.add_entity(
                project, body.name, body.layer, body.kind, body.source, body.domain, body.description
            )
            return {entity.id: (round(body.position.x), round(body.position.y))} if body.position else {}

        return change(action, rev)

    @app.patch("/api/entities/{entity_id}")
    def update_entity(entity_id: UUID, body: Changes, rev: Revision = None) -> dict[str, Any]:
        def action(project: Project) -> None:
            name = entity_name(project, entity_id)
            changes = dict(body)
            new_name = changes.pop("name", None)
            if changes:
                edit.update_entity(project, name, changes)
            if new_name and new_name != name:
                edit.rename_entity(project, name, new_name)

        return change(action, rev, prune=True, previewable=True)

    @app.patch("/api/entities")
    def update_entities(body: BulkChange, rev: Revision = None) -> dict[str, Any]:
        """The same change (domain, layer...) to several tables at once: one change, one undo."""
        if "name" in body.changes:
            raise HTTPException(400, {"message": "rename tables one at a time"})

        def action(project: Project) -> None:
            for name in [entity_name(project, i) for i in body.ids]:
                edit.update_entity(project, name, dict(body.changes))

        return change(action, rev, prune=True, previewable=True)

    @app.post("/api/entities/remove")
    def remove_entities(body: BulkRemove, rev: Revision = None) -> dict[str, Any]:
        def action(project: Project) -> None:
            for name in [entity_name(project, i) for i in body.ids]:
                edit.remove_entity(project, name)

        return change(action, rev, prune=True, previewable=True)

    @app.delete("/api/entities/{entity_id}")
    def remove_entity(entity_id: UUID, rev: Revision = None) -> dict[str, Any]:
        return change(lambda p: edit.remove_entity(p, entity_name(p, entity_id)), rev, prune=True, previewable=True)

    @app.post("/api/entities/{entity_id}/columns/copy")
    def copy_columns(entity_id: UUID, body: CopyColumns, rev: Revision = None) -> dict[str, Any]:
        """Bring in columns of other tables: same names, types, descriptions and keys' nullability,
        each mapped to where it comes from (raw tables are loaded, not mapped). One change, one undo."""

        def action(project: Project) -> None:
            name = entity_name(project, entity_id)
            target = project.model.entity(name)
            have = {a.name.lower() for a in target.attributes}
            for ref in body.columns:
                source_entity, _, column_name = ref.partition(".")
                source = project.model.entity(source_entity)
                if source is None:
                    raise edit.EditError(f"there is no table {source_entity}")
                column = next((a for a in source.attributes if a.name.lower() == column_name.lower()), None)
                if column is None:
                    raise edit.EditError(f"{source_entity} has no column {column_name}")
                if column.name.lower() in have:
                    continue
                have.add(column.name.lower())
                edit.add_column(
                    project, name, column.name, str(column.type), False, None, column.description, column.pii, None
                )
                if target.layer.value != "raw":
                    edit.set_mapping(project, name, column.name, [f"{source.name}.{column.name}"])

        return change(action, rev)

    @app.post("/api/entities/{entity_id}/columns")
    def add_column(entity_id: UUID, body: NewColumn, rev: Revision = None) -> dict[str, Any]:
        def action(project: Project) -> None:
            edit.add_column(
                project,
                entity_name(project, entity_id),
                body.name,
                body.type,
                body.primary_key,
                body.nullable,
                body.description,
                body.pii,
                body.classification,
            )

        return change(action, rev)

    @app.patch("/api/entities/{entity_id}/columns/{column_id}")
    def update_column(entity_id: UUID, column_id: UUID, body: Changes, rev: Revision = None) -> dict[str, Any]:
        def action(project: Project) -> None:
            entity, column = entity_name(project, entity_id), column_name(project, entity_id, column_id)
            changes = dict(body)
            new_name = changes.pop("name", None)
            if changes:
                edit.update_column(project, entity, column, changes)
            if new_name and new_name != column:
                edit.rename_column(project, entity, column, new_name)

        return change(action, rev, previewable=True)

    @app.put("/api/entities/{entity_id}/columns/order")
    def put_column_order(entity_id: UUID, body: ColumnOrder, rev: Revision = None) -> dict[str, Any]:
        def action(project: Project) -> None:
            names = [column_name(project, entity_id, column_id) for column_id in body.columns]
            edit.order_columns(project, entity_name(project, entity_id), names)

        return change(action, rev)

    @app.delete("/api/entities/{entity_id}/columns/{column_id}")
    def remove_column(entity_id: UUID, column_id: UUID, rev: Revision = None) -> dict[str, Any]:
        def action(project: Project) -> None:
            edit.remove_column(project, entity_name(project, entity_id), column_name(project, entity_id, column_id))

        return change(action, rev, previewable=True)

    @app.post("/api/relationships")
    def add_relationship(body: NewRelationship, rev: Revision = None) -> dict[str, Any]:
        def action(project: Project) -> None:
            keys = [(k.from_, k.to) for k in body.keys]
            edit.add_relationship(
                project, body.from_, body.to, keys, body.cardinality, body.name, body.optional, body.role
            )

        return change(action, rev)

    @app.patch("/api/relationships/{relationship_id}")
    def update_relationship(relationship_id: UUID, body: Changes, rev: Revision = None) -> dict[str, Any]:
        return change(lambda p: edit.update_relationship(p, relationship_id, body), rev, previewable=True)

    @app.delete("/api/relationships/{relationship_id}")
    def remove_relationship(relationship_id: UUID, rev: Revision = None) -> dict[str, Any]:
        return change(lambda p: edit.remove_relationship(p, relationship_id), rev, previewable=True)

    from murakumo import ci as ci_module

    checks = ci_module.Runner(root)

    @app.get("/api/ci")
    def get_ci(provider: str = "github", deploy: bool = False, preview: bool = False) -> dict[str, Any]:
        """The CI file: there, as Murakumo would write it, or changed; and the last local run of its checks."""
        project, _ = _blocked(root)
        if project is None:
            raise HTTPException(409, {"message": "the project did not load"})
        try:
            return {**ci_module.status(project, provider, deploy, preview), "run": checks.run.as_dict()}
        except ci_module.CIError as exc:
            raise HTTPException(400, {"message": str(exc)}) from None

    @app.post("/api/ci")
    def post_ci(body: CIBody) -> dict[str, Any]:
        """Write the CI file at the repository root. A file with changes of its own needs force."""
        with lock:
            project, _ = _blocked(root)
            if project is None:
                raise HTTPException(409, {"message": "the project did not load"})
            try:
                written = ci_module.write(project, body.provider, body.deploy, body.force, body.preview)
                return {**written, "run": checks.run.as_dict()}
            except ci_module.CIError as exc:
                raise HTTPException(409, {"message": str(exc)}) from None

    def settings() -> dict[str, Any]:
        from murakumo.manifest import ModellingPattern, Orchestrator

        project, _ = _blocked(root)
        if project is None:
            raise HTTPException(409, {"message": "the project did not load"})
        m = project.manifest
        return {
            "warehouse": m.targets.warehouse.type.value,
            "orchestrator": m.generation.orchestrator.value,
            "pattern": m.modelling.pattern.value,
            "python_engine": m.generation.python_engine,
            "plain_sql": m.generation.plain_sql,
            "run_log": m.quality.run_log,
            "raw_text": m.modelling.raw_columns == "text",
            "base_branch": m.collaboration.base_branch,
            "personal_schemas": m.collaboration.personal_schemas,
            "personal_database": m.collaboration.personal_database,
            "environments": [e.model_dump() for e in m.targets.environments],
            "choices": {
                "warehouses": [w.value for w in Warehouse],
                "orchestrators": [o.value for o in Orchestrator],
                "patterns": [p.value for p in ModellingPattern],
                "python_engines": ["none", "polars", "pyspark"],
            },
        }

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        return settings()

    @app.put("/api/settings")
    def put_settings(body: SettingsBody) -> dict[str, Any]:
        """Change the project's settings in murakumo.yaml, keeping its comments; undoable."""
        from murakumo.manifest import ModellingPattern, Orchestrator
        from murakumo.manifest_edit import ManifestEditError, write_section
        from murakumo.scaffold import configure_project

        given = body.model_dump(exclude_unset=True)
        with lock:
            before = history_module.take(root)
            try:
                changed = configure_project(
                    root,
                    body.warehouse,
                    Orchestrator(body.orchestrator) if body.orchestrator else None,
                    ModellingPattern(body.pattern) if body.pattern else None,
                )
                paths = {
                    "python_engine": ("generation", "python_engine"),
                    "plain_sql": ("generation", "plain_sql"),
                    "run_log": ("quality", "run_log"),
                    "base_branch": ("collaboration", "base_branch"),
                    "personal_schemas": ("collaboration", "personal_schemas"),
                    "personal_database": ("collaboration", "personal_database"),
                }
                current = settings()
                if "raw_text" in given and given["raw_text"] is not None and given["raw_text"] != current["raw_text"]:
                    value = "text" if given["raw_text"] else "typed"
                    write_section(root, ("modelling", "raw_columns"), value)
                    changed.append(f"raw columns: {'text' if current['raw_text'] else 'typed'} → {value}")
                for key, path in paths.items():
                    if key in given and given[key] is not None and given[key] != current[key]:
                        try:
                            write_section(root, path, given[key])
                        except ManifestEditError:  # no such section yet: add it, with just this key
                            write_section(root, path[:1], {path[1]: given[key]})
                        changed.append(f"{key.replace('_', ' ')}: {current[key]} → {given[key]}")
                if body.environments is not None:
                    wanted = [
                        {k: v for k, v in e.model_dump().items() if v not in (None, "")} for e in body.environments
                    ]
                    if wanted != [{k: v for k, v in e.items() if v not in (None, "")} for e in current["environments"]]:
                        write_section(root, ("targets", "environments"), wanted)
                        changed.append("environments: " + ", ".join(e["name"] for e in wanted))
            except (ValueError, ManifestEditError) as exc:
                raise HTTPException(400, {"message": str(exc)}) from None
            if changed:
                history.record("settings: " + "; ".join(changed), before, history_module.take(root))
            return {**settings(), "changed": changed, "state": state(root)}

    @app.get("/api/env/provision")
    def get_provision() -> dict[str, str]:
        """What an administrator runs once to create the environments (`murakumo env provision`)."""
        from murakumo.environments import provision

        project, _ = _blocked(root)
        if project is None:
            raise HTTPException(409, {"message": "the project did not load"})
        return {"sql": provision(project)}

    @app.get("/api/setup")
    def get_setup(connect: bool = False) -> dict[str, Any]:
        """What this project needs on this machine (`murakumo setup`): each check, and how to fix it."""
        from murakumo.guide import Status, run_checks

        project, _ = _blocked(root)
        if project is None:
            raise HTTPException(409, {"message": "the project did not load"})
        checks = run_checks(project, connect=connect)
        return {"ready": not any(c.status is Status.TODO for c in checks), "checks": [c.as_dict() for c in checks]}

    # Files added from the canvas: data to load, dictionaries to import. They go to data/ in the
    # project, which git ignores, so nobody commits a client's data by accident.
    data_dir = root / UPLOADS_DIR

    def upload_path(name: str) -> Path:
        if not UPLOAD_NAME.match(name) or name.startswith("."):
            raise HTTPException(400, {"message": "name the file with letters, digits, dots, dashes and underscores"})
        if Path(name).suffix.lower() not in UPLOAD_TYPES:
            raise HTTPException(400, {"message": f"add {', '.join(sorted(UPLOAD_TYPES))} files"})
        return data_dir / name

    def ignore_uploads() -> None:
        ignore = root / ".gitignore"
        text = ignore.read_text(encoding="utf-8") if ignore.is_file() else ""
        if not any(line.strip() in (f"{UPLOADS_DIR}/", UPLOADS_DIR, f"/{UPLOADS_DIR}/") for line in text.splitlines()):
            ignore.write_text(
                text
                + ("" if text.endswith("\n") or not text else "\n")
                + f"# Files added from the canvas (data to load, dictionaries)\n{UPLOADS_DIR}/\n",
                encoding="utf-8",
            )

    def describe(path: Path) -> dict[str, Any]:
        return {"name": path.name, "path": f"{UPLOADS_DIR}/{path.name}", "size": path.stat().st_size}

    @app.get("/api/uploads")
    def list_uploads() -> dict[str, Any]:
        files = sorted(p for p in data_dir.glob("*") if p.is_file()) if data_dir.is_dir() else []
        return {"files": [describe(p) for p in files if p.suffix.lower() in UPLOAD_TYPES]}

    @app.put("/api/uploads/{name}")
    async def put_upload(name: str, request: Request) -> dict[str, Any]:
        """Add a file, sent as its raw bytes. Only application/octet-stream is accepted, which a page
        on another site cannot send here without the server's consent."""
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/octet-stream":
            raise HTTPException(415, {"message": "send the file as application/octet-stream"})
        path = upload_path(name)
        data_dir.mkdir(exist_ok=True)
        ignore_uploads()
        partial = path.with_name(f".{path.name}.part")
        size = 0
        with partial.open("wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > UPLOAD_LIMIT:
                    out.close()
                    partial.unlink(missing_ok=True)
                    raise HTTPException(413, {"message": f"files up to {UPLOAD_LIMIT // 1_000_000} MB"})
                out.write(chunk)
        partial.replace(path)
        return describe(path)

    @app.delete("/api/uploads/{name}")
    def delete_upload(name: str) -> dict[str, Any]:
        path = upload_path(name)
        path.unlink(missing_ok=True)
        return list_uploads()

    from murakumo import jobs as jobs_module

    @app.get("/api/env")
    def get_env(environment: str | None = None, prefix: str | None = None) -> dict[str, Any]:
        """Where an environment's builds go (`murakumo env`): the prefix, and each schema's tables."""
        from murakumo.environments import EnvError, connect, describe, developer, placement_for

        project, _ = _blocked(root)
        if project is None:
            raise HTTPException(409, {"message": "the project did not load"})
        if prefix and not jobs_module.PREFIX.match(prefix):
            raise HTTPException(400, {"message": "a schema prefix is lower case and ends in _"})
        warehouse = project.manifest.targets.warehouse.type
        try:
            placement = placement_for(project, environment, warehouse, prefix)
        except EnvError as exc:
            raise HTTPException(400, {"message": str(exc)}) from None
        out: dict[str, Any] = {
            "developer": developer(),
            "environment": placement.environment,
            "prefix": placement.prefix,
            "single": placement.single,
            "database": placement.database,
            "where": placement.describe(),
            "warehouse": warehouse.value,
            "schemas": [],
        }
        try:
            connection = connect(project, read_only=True, database=placement.database)
        except EnvError as exc:
            return {**out, "reason": str(exc)}
        try:
            out["schemas"] = describe(project, connection, placement)
        finally:
            connection.close()
        return out

    @app.get("/api/diff")
    def get_diff(ref: str = "") -> dict[str, Any]:
        """What changed in the model since a branch, tag or commit (`murakumo diff`), and what it reaches."""
        from murakumo import gitsync, impact

        project, _ = _blocked(root)
        if project is None:
            raise HTTPException(409, {"message": "the project did not load"})
        if ref and not re.match(r"^[\w./~^-]{1,200}$", ref):
            raise HTTPException(400, {"message": "name a branch, tag or commit"})
        try:
            w = gitsync.where(project)
            against = ref or w.ref
            old = gitsync.model_at(project, w, against)
        except gitsync.GitError as exc:
            raise HTTPException(409, {"message": str(exc)}) from None
        result = impact.report(project, old, project.model)
        return {"against": against, **result.as_dict(), "markdown": impact.markdown(result, against)}

    @app.get("/api/runs")
    def get_runs(environment: str | None = None, prefix: str | None = None, limit: int = 20) -> dict[str, Any]:
        """What the runs recorded in the audit schema: by day, each run, loads, failed tests, and
        when each schedule last ran."""
        from murakumo import runlog

        project, _ = _blocked(root)
        if project is None:
            raise HTTPException(409, {"message": "the project did not load"})
        if prefix and not jobs_module.PREFIX.match(prefix):
            raise HTTPException(400, {"message": "a schema prefix is lower case and ends in _"})
        return runlog.read(project, environment, prefix, max(1, min(limit, 200)))

    jobs = jobs_module.Jobs(root)

    def job_call(action: Callable[[], Any]) -> Any:
        try:
            return action()
        except jobs_module.JobError as exc:
            raise HTTPException(409, {"message": str(exc)}) from None

    @app.post("/api/jobs")
    def start_job(body: JobBody) -> dict[str, Any]:
        """Start a long command (build, verify, backfill...) in the background; follow it with GET."""
        return job_call(lambda: jobs.start(body.kind, body.options).as_dict())

    @app.get("/api/jobs")
    def list_jobs() -> dict[str, Any]:
        return {"jobs": [{**j.as_dict(), "lines": j.lines[-3:]} for j in reversed(jobs.jobs)]}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, since: int = 0) -> dict[str, Any]:
        """The job and its output from line `since` on, so the canvas fetches only what is new."""
        return job_call(lambda: jobs.get(job_id).as_dict(since))

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, Any]:
        return job_call(lambda: jobs.cancel(job_id).as_dict())

    @app.post("/api/ci/run")
    def run_ci() -> dict[str, Any]:
        """Run the CI's checks on this machine, in the background; GET /api/ci/run follows them."""
        return checks.start().as_dict()

    @app.get("/api/ci/run")
    def get_ci_run() -> dict[str, Any]:
        return checks.run.as_dict()

    from murakumo import gitsync

    def git_call(action: Callable[[], Any]) -> Any:
        try:
            return action()
        except gitsync.GitError as exc:
            raise HTTPException(409, {"message": str(exc)}) from None

    @app.get("/api/git")
    def get_git(refresh: bool = False) -> dict[str, Any]:
        """Branch, parent branch, how far apart, and the model changes on each side."""
        project, _ = _blocked(root)
        if project is None:
            raise HTTPException(409, {"message": "the project did not load"})
        from murakumo import branches as branches_module
        from murakumo.environments import developer, placement_for

        info = git_call(lambda: gitsync.status(project, refresh=refresh))
        personal = project.manifest.collaboration.personal_environment
        warehouse = project.manifest.targets.warehouse.type
        return {
            **info,
            "developer": developer(),
            "personal_environment": personal,
            "flow": [{"environment": f.environment, "branch": f.branch} for f in branches_module.flow(project)],
            "personal_prefix": placement_for(project, None, warehouse).prefix,
            "personal_schema": placement_for(project, None, warehouse).single,
            "personal_where": placement_for(project, None, warehouse).describe(),
        }

    @app.post("/api/git/start")
    def post_git_start() -> dict[str, Any]:
        """Create the environments' branches (dev, stage) from main if missing (`murakumo branch start`)."""
        from murakumo import branches as branches_module

        project, _ = _blocked(root)
        if project is None:
            raise HTTPException(409, {"message": "the project did not load"})
        return {"done": git_call(lambda: branches_module.start(project))}

    @app.post("/api/git/branch")
    def post_git_branch(body: BranchBody) -> dict[str, Any]:
        """Start new work on a branch of its own, from the latest dev (`murakumo branch new`)."""
        from murakumo import branches as branches_module

        with lock:
            project, _ = _blocked(root)
            if project is None:
                raise HTTPException(409, {"message": "the project did not load"})
            branch = git_call(lambda: branches_module.new(project, body.kind, body.name))
            return {"branch": branch, "state": state(root)}

    @app.get("/api/git/promotion")
    def get_promotion(branch: str) -> dict[str, Any]:
        """What promoting an environment's branch to the next would move (`murakumo branch promote`)."""
        from murakumo import branches as branches_module

        project, _ = _blocked(root)
        if project is None:
            raise HTTPException(409, {"message": "the project did not load"})
        return git_call(lambda: branches_module.promotion(project, branch))

    @app.post("/api/git/sync")
    def post_sync(resume: bool = False) -> dict[str, Any]:
        """Bring in the parent branch, as `murakumo sync` does. Returns the result and the new state."""
        with lock:
            result = load_project(root)
            if result.project is None:
                raise HTTPException(409, {"message": "the project did not load"})
            done = git_call(lambda: gitsync.sync(result.project, resume=resume))
            return {"sync": done.as_dict(), "state": state(root)}

    @app.post("/api/git/abort")
    def post_abort() -> dict[str, Any]:
        with lock:
            result = load_project(root)
            git_call(lambda: gitsync.abort(result.project))
            return {"state": state(root)}

    @app.post("/api/git/commit")
    def post_commit(body: CommitBody) -> dict[str, Any]:
        """Commit everything in the project's folder."""
        with lock:
            result = load_project(root)
            if result.project is None:
                raise HTTPException(409, {"message": "the project did not load"})
            return {"commit": git_call(lambda: gitsync.commit(result.project, body.message))}

    @app.get("/api/pipeline-docs", response_class=HTMLResponse)
    def get_docs() -> HTMLResponse:
        """The pipeline docs page, as `murakumo generate` writes it to generated/docs/index.html."""
        from murakumo.generate import docs
        from murakumo.generate.plan import build_plan
        from murakumo.generate.targets import target_for

        project, blocked = _blocked(root)
        if project is None or blocked:
            raise HTTPException(409, {"message": blocked or "the project did not load"})
        plan = build_plan(project, target_for(project.manifest.targets.warehouse.type))
        return HTMLResponse(docs.html(project, plan))

    @app.get("/api/lineage")
    def get_lineage(entity: str, column: str | None = None) -> dict[str, Any]:
        """Where a column (or every column of an entity) comes from, and what uses it."""
        project, _ = _blocked(root)
        if project is None:
            raise HTTPException(409, {"message": "the project did not load"})
        try:
            name, attribute = resolve_ref(project, f"{entity}.{column}" if column else entity)
        except ValueError as exc:
            raise HTTPException(404, {"message": str(exc)}) from None
        return trace(project, name, attribute)

    @app.post("/api/expressions/check")
    def post_expression_check(body: ExpressionBody) -> dict[str, Any]:
        """What an expression reads, whether it is valid, the type it returns and its warehouse SQL."""
        project, _ = _blocked(root)
        if project is None:
            raise HTTPException(409, {"message": "the project did not load"})
        return check_expression(project, body.expression, body.sources, body.entity).as_dict()

    @app.post("/api/expressions/preview")
    def post_expression_preview(body: ExpressionBody) -> dict[str, Any]:
        """The expression run on a few made-up rows, in DuckDB in memory."""
        project, _ = _blocked(root)
        if project is None:
            raise HTTPException(409, {"message": "the project did not load"})
        return preview_expression(project, body.expression, body.sources, body.entity)

    @app.post("/api/entities/{entity_id}/calculated")
    def add_calculated(entity_id: UUID, body: CalculatedColumn, rev: Revision = None) -> dict[str, Any]:
        def action(project: Project) -> None:
            edit.add_calculated_column(project, entity_name(project, entity_id), body.name, body.expression, body.type)

        return change(action, rev)

    @app.post("/api/sources")
    def add_source(body: NewSource, rev: Revision = None) -> dict[str, Any]:
        """Declare a source in murakumo.yaml. A database is named by an environment variable."""

        def action(project: Project) -> None:
            try:
                sources_module.add_source(project, body.name, body.kind, body.location)
            except sources_module.SourceError as exc:
                raise edit.EditError(str(exc)) from None

        return change(action, rev)

    @app.delete("/api/sources/{name}")
    def remove_source(name: str, rev: Revision = None) -> dict[str, Any]:
        """Remove a source no table uses."""

        def action(project: Project) -> None:
            try:
                sources_module.remove_source(project, name)
            except sources_module.SourceError as exc:
                raise edit.EditError(str(exc)) from None

        return change(action, rev, prune=True)

    @app.get("/api/sources/{name}/browse")
    def browse_source(name: str) -> dict[str, Any]:
        """A database source's schemas, tables and columns. Reads the catalogue only, never rows."""
        project, _ = _blocked(root)
        if project is None:
            raise HTTPException(409, {"message": "the project did not load"})
        try:
            return {"schemas": sources_module.browse_source(project, name)}
        except sources_module.SourceError as exc:
            raise HTTPException(400, {"message": str(exc)}) from None

    @app.post("/api/sources/{name}/import/preview")
    def preview_import(name: str, body: SourceImportBody) -> dict[str, Any]:
        """What importing would change, without writing anything."""
        project, _ = _blocked(root)
        if project is None:
            raise HTTPException(409, {"message": "the project did not load"})
        try:
            return {"reports": sources_module.import_source(project, name, body.tables, body.prune)}
        except sources_module.SourceError as exc:
            raise HTTPException(400, {"message": str(exc)}) from None

    @app.post("/api/sources/{name}/import")
    def run_import(name: str, body: SourceImportBody, rev: Revision = None) -> dict[str, Any]:
        reports: list[dict[str, Any]] = []

        def action(project: Project) -> None:
            try:
                reports.extend(sources_module.import_source(project, name, body.tables, body.prune))
            except sources_module.SourceError as exc:
                raise edit.EditError(str(exc)) from None
            problems = [d for r in reports for d in r["diagnostics"] if d["level"] == "error"]
            if problems:
                raise edit.EditError("; ".join(d["message"] for d in problems))

        new_state = change(action, rev, prune=body.prune)
        return {**new_state, "reports": reports}

    def schedule_change(action: Callable[[Project], Any]) -> Callable[[Project], Any]:
        def guarded(project: Project) -> Any:
            try:
                return action(project)
            except schedules_module.ScheduleError as exc:
                raise edit.EditError(str(exc)) from None

        return guarded

    @app.post("/api/schedules")
    def add_schedule(body: NewSchedule, rev: Revision = None) -> dict[str, Any]:
        return change(
            schedule_change(
                lambda p: schedules_module.add_schedule(p, body.name, body.cron, body.select, body.description)
            ),
            rev,
        )

    @app.patch("/api/schedules/{name}")
    def update_schedule(name: str, body: Changes, rev: Revision = None) -> dict[str, Any]:
        return change(schedule_change(lambda p: schedules_module.update_schedule(p, name, body)), rev)

    @app.delete("/api/schedules/{name}")
    def remove_schedule(name: str, rev: Revision = None) -> dict[str, Any]:
        return change(schedule_change(lambda p: schedules_module.remove_schedule(p, name)), rev)

    @app.put("/api/orchestration/timezone")
    def put_timezone(body: TimezoneBody, rev: Revision = None) -> dict[str, Any]:
        return change(schedule_change(lambda p: schedules_module.set_timezone(p, body.timezone)), rev)

    @app.post("/api/domains")
    def add_domain(body: NewDomain, rev: Revision = None) -> dict[str, Any]:
        return change(lambda p: edit.add_domain(p, body.name, body.owner, body.description), rev)

    @app.patch("/api/domains/{name}")
    def update_domain(name: str, body: Changes, rev: Revision = None) -> dict[str, Any]:
        return change(lambda p: edit.update_domain(p, name, body), rev)

    @app.delete("/api/domains/{name}")
    def remove_domain(name: str, rev: Revision = None) -> dict[str, Any]:
        return change(lambda p: edit.remove_domain(p, name), rev)

    @app.post("/api/undo")
    def undo() -> dict[str, Any]:
        """Take back the last change the canvas made."""
        return travel(history.undo, "Undid")

    @app.post("/api/redo")
    def redo() -> dict[str, Any]:
        return travel(history.redo, "Redid")

    def travel(step: Callable[[Path], str], done: str) -> dict[str, Any]:
        with lock:
            try:
                what = step(root)
            except history_module.HistoryError as exc:
                raise HTTPException(409, {"message": str(exc), "state": state(root)}) from None
            return {**state(root), "message": f"{done}: {what}"}

    @app.put("/api/canvas")
    def put_canvas(body: CanvasSettings) -> dict[str, Any]:
        """Collapsed tables, colour labels, notes and diagrams: kept in the layout file, and undoable."""
        with lock:
            result = load_project(root)
            if result.project is None:
                raise HTTPException(409, {"message": "the project did not load"})
            given = {k: v for k, v in body.model_dump(mode="json").items() if k in body.model_fields_set}
            label = given.pop("label", None) or "change the canvas"
            settings = {**load_canvas(root), **given}
            if "labels" in given:  # a removed label leaves no table coloured with it
                names = {lb["name"] for lb in given["labels"]}
                settings["colours"] = {k: v for k, v in (settings.get("colours") or {}).items() if v in names}
            before = history_module.take(root)
            save_layout(result.project, load_layout(root), settings)
            history.record(label, before, history_module.take(root))
            return state(root)

    @app.put("/api/layout")
    def put_layout(body: Layout) -> dict[str, str]:
        """Save positions. No revision check: moving boxes never conflicts with a model change."""
        with lock:
            result = load_project(root)
            if result.project is None:
                raise HTTPException(409, {"message": "the project did not load"})
            positions = load_layout(root)
            positions.update({k: (round(v.x), round(v.y)) for k, v in body.positions.items()})
            save_layout(result.project, positions)
            return {"revision": revision(root)}

    if static is not None:
        app.mount("/", StaticFiles(directory=static, html=True), name="canvas")
    return app
