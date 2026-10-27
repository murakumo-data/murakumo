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

import hashlib
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from murakumo import __version__, edit
from murakumo.diagnostics import Diagnostic, Level
from murakumo.ir.model import Cardinality, Classification, EntityKind, Layer
from murakumo.ir.types import TypeName
from murakumo.manifest import MANIFEST_FILE
from murakumo.project import MODEL_DIR, Project, load_layout, load_project, save_layout, save_model
from murakumo.validate import validate_project

REVISION_HEADER = "X-Murakumo-Revision"
LOCAL_HOSTS = ["localhost", "127.0.0.1"]

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


# State ------------------------------------------------------------------------------------------


def revision(root: Path) -> str:
    """A fingerprint of the manifest and every model file: path, size and modification time."""
    digest = hashlib.sha1()
    files = [root / MANIFEST_FILE, *sorted((root / MODEL_DIR).rglob("*"))]
    for path in files:
        if path.is_file():
            stat = path.stat()
            digest.update(f"{path.relative_to(root)}:{stat.st_size}:{stat.st_mtime_ns};".encode())
    return digest.hexdigest()[:16]


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
    }


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
        "diagnostics": [_diagnostic(d, project, root) for d in diagnostics],
        "choices": {
            "sources": sources,
            "layers": [v.value for v in Layer],
            "kinds": [v.value for v in EntityKind],
            "cardinalities": [v.value for v in Cardinality],
            "classifications": [v.value for v in Classification],
            "types": [v.value for v in TypeName],
        },
    }


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
    app = FastAPI(
        title="Murakumo canvas API", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json"
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)

    def change(action: Callable[[Project], Any], expected: str | None, prune: bool = False) -> dict[str, Any]:
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
            try:
                placed = action(project)
            except edit.EditError as exc:
                raise HTTPException(400, {"message": str(exc)}) from None
            positions = load_layout(root)
            if isinstance(placed, dict):  # an action that adds an entity may say where to draw it
                positions.update(placed)
            save_model(project, prune=prune)
            save_layout(project, positions)  # drops the positions of removed entities
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
        """Cheap to poll: the canvas reloads the state when this changes."""
        return {"revision": revision(root)}

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

        return change(action, rev, prune=True)

    @app.delete("/api/entities/{entity_id}")
    def remove_entity(entity_id: UUID, rev: Revision = None) -> dict[str, Any]:
        return change(lambda p: edit.remove_entity(p, entity_name(p, entity_id)), rev, prune=True)

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

        return change(action, rev)

    @app.delete("/api/entities/{entity_id}/columns/{column_id}")
    def remove_column(entity_id: UUID, column_id: UUID, rev: Revision = None) -> dict[str, Any]:
        def action(project: Project) -> None:
            edit.remove_column(project, entity_name(project, entity_id), column_name(project, entity_id, column_id))

        return change(action, rev)

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
        return change(lambda p: edit.update_relationship(p, relationship_id, body), rev)

    @app.delete("/api/relationships/{relationship_id}")
    def remove_relationship(relationship_id: UUID, rev: Revision = None) -> dict[str, Any]:
        return change(lambda p: edit.remove_relationship(p, relationship_id), rev)

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
