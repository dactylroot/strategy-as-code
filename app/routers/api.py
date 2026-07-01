from __future__ import annotations
import io
import re
import zipfile
import tempfile
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse, Response
from watchfiles import awatch
from pydantic import BaseModel

from ..config import settings
from .. import session_store
from ..models import (
    FeatureUpdate, NewFeature, RoadmapUpdate, RoadmapFeaturesUpdate, NewRelease,
    BugCreate, BugUpdate, FeatureStatus, VersionBucket,
)
from ..parsers import product as product_parser
from ..parsers import about as about_parser
from ..parsers import bugs as bugs_parser
from .. import wbs as wbs_module
from ..template_env import templates

router = APIRouter()

_EMPTY_BUGS = bugs_parser._EMPTY_BUGS


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _session(request: Request) -> session_store.Session | None:
    return session_store.get(request.headers.get("X-Session-ID"))


def _require_session(request: Request) -> session_store.Session:
    s = _session(request)
    if s is None:
        raise HTTPException(status_code=401, detail="session_expired")
    return s


# ── Product / Features ───────────────────────────────────────────────────────

@router.get("/product")
def get_product(request: Request):
    s = _session(request)
    if s:
        return product_parser._parse_text(s.get_file("PRODUCT.MD"))
    return product_parser.parse(settings.product_md)


@router.patch("/features/{wbs:path}")
def patch_feature(wbs: str, body: FeatureUpdate, request: Request):
    s = _session(request)
    if body.name is not None and (
        not body.name.strip() or "|" in body.name or len(body.name.splitlines()) > 1
    ):
        raise HTTPException(status_code=400, detail="Feature name cannot be empty, contain '|', or span multiple lines")
    score_explicit = 'value' in body.model_fields_set or 'effort' in body.model_fields_set

    def _apply(text: str) -> str:
        if body.name is not None:
            text = product_parser.transform_feature_name(text, wbs, body.name)
        if body.status is not None:
            text = product_parser.transform_feature_status(text, wbs, body.status)
        if score_explicit:
            text = product_parser.transform_feature_score(text, wbs, body.value, body.effort)
        if body.notes is not None:
            text = product_parser.transform_feature_notes(text, wbs, body.notes)
        if body.flagged is not None:
            text = product_parser.transform_feature_flagged(text, wbs, body.flagged)
        return text

    try:
        if s:
            text = _apply(s.get_file("PRODUCT.MD"))
            s.set_file("PRODUCT.MD", text)
            updated = {"product_md": text}
        else:
            lock = product_parser._lock_for(settings.product_md)
            with lock:
                text = _apply(settings.product_md.read_text(encoding="utf-8"))
                product_parser._atomic_write(settings.product_md, text)
            updated = {}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    if _is_htmx(request):
        text_src = s.get_file("PRODUCT.MD") if s else settings.product_md.read_text(encoding="utf-8")
        product = product_parser._parse_text(text_src)
        feature = next(
            (f for area in product.wbs_areas for sa in area.sub_areas for f in sa.features if f.wbs == wbs),
            None,
        )
        if feature is None:
            raise HTTPException(status_code=404, detail=f"Feature {wbs} not found after update")
        return templates.TemplateResponse(request, "partials/feature_row.html", {"feature": feature})
    return {"ok": True, **updated}


class MoveFeatureBody(BaseModel):
    target_prefix: str


@router.post("/features/{wbs:path}/move")
def move_feature(wbs: str, body: MoveFeatureBody, request: Request):
    s = _session(request)
    try:
        if s:
            product_text = s.get_file("PRODUCT.MD")
            product_text, new_feature = product_parser.transform_move_feature(product_text, wbs, body.target_prefix)
            s.set_file("PRODUCT.MD", product_text)
            return {"ok": True, "new_wbs": new_feature.wbs, "updated": {"product_md": product_text}}
        else:
            new_feature = product_parser.move_feature(settings.product_md, wbs, body.target_prefix)
            return {"ok": True, "new_wbs": new_feature.wbs}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/features/{wbs:path}")
def delete_feature(wbs: str, request: Request):
    s = _session(request)
    try:
        if s:
            text = product_parser.transform_delete_feature(s.get_file("PRODUCT.MD"), wbs)
            s.set_file("PRODUCT.MD", text)
            updated = {"product_md": text}
        else:
            lock = product_parser._lock_for(settings.product_md)
            with lock:
                text = product_parser.transform_delete_feature(
                    settings.product_md.read_text(encoding="utf-8"), wbs
                )
                product_parser._atomic_write(settings.product_md, text)
            updated = {}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True, **updated}


@router.post("/features")
def post_feature(body: NewFeature, request: Request):
    s = _session(request)
    if "|" in body.name or len(body.name.splitlines()) > 1:
        raise HTTPException(status_code=400, detail="Feature name cannot contain '|' or span multiple lines")
    if "|" in body.notes:
        raise HTTPException(status_code=400, detail="Feature notes cannot contain '|'")
    try:
        if s:
            text = s.get_file("PRODUCT.MD")
            text, feature = product_parser.transform_add_feature(text, body)
            s.set_file("PRODUCT.MD", text)
        else:
            feature = product_parser.add_feature(settings.product_md, body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if _is_htmx(request):
        return templates.TemplateResponse(request, "partials/feature_row.html", {"feature": feature})
    return feature


# ── Roadmap ───────────────────────────────────────────────────────────────────

@router.get("/about")
def get_about(request: Request):
    s = _session(request)
    if s:
        return about_parser._parse_text(s.get_file("ABOUT.MD"))
    return about_parser.parse(settings.about_md)


@router.put("/roadmap")
def put_roadmap(body: RoadmapUpdate, request: Request):
    s = _session(request)
    if s:
        text = s.get_file("ABOUT.MD")
        s.set_file("ABOUT.MD", about_parser.transform_update_roadmap(text, body))
    else:
        about_parser.update_roadmap(settings.about_md, body)
    return {"ok": True}


@router.put("/roadmap/features")
def put_roadmap_features(body: RoadmapFeaturesUpdate, request: Request):
    from ..models import FeatureStatus
    s = _session(request)

    all_planned_wbs = body.planned_wbs + [w for b in body.planned_buckets for w in b.wbs]
    all_active = set(body.in_progress_wbs) | set(all_planned_wbs)

    if s:
        product_text = s.get_file("PRODUCT.MD")
        prod = product_parser._parse_text(product_text)
        wbs_to_feature = {f.wbs: f for area in prod.wbs_areas for sa in area.sub_areas for f in sa.features}
        wbs_to_prefix  = {f.wbs: sa.wbs_prefix for area in prod.wbs_areas for sa in area.sub_areas for f in sa.features}

        for wbs, f in wbs_to_feature.items():
            if f.status == FeatureStatus.planned and wbs not in all_active:
                try:
                    product_text = product_parser.transform_feature_status(product_text, wbs, FeatureStatus.idea)
                except Exception:
                    pass
        _promotable = {FeatureStatus.gap, FeatureStatus.idea}
        for wbs in all_active:
            f = wbs_to_feature.get(wbs)
            if f and f.status in _promotable:
                try:
                    product_text = product_parser.transform_feature_status(product_text, wbs, FeatureStatus.planned)
                except Exception:
                    pass
        s.set_file("PRODUCT.MD", product_text)

        prod2 = product_parser._parse_text(product_text)
        sa_label = {sa.wbs_prefix: f"{sa.wbs_prefix} {sa.title}" for area in prod2.wbs_areas for sa in area.sub_areas}
        ip_prefixes: set[str] = {wbs_to_prefix[w] for w in body.in_progress_wbs if w in wbs_to_prefix}

        about_text = s.get_file("ABOUT.MD")
        pre_pl = about_parser._parse_text(about_text).roadmap_section("Planned")
        existing_bucket_for_wbs: dict[str, str] = {
            w: b.label
            for b in (pre_pl.buckets if pre_pl else [])
            for w in b.items
        }

        labeled_buckets_map: dict[str, list[str]] = {}
        bucket_wbs_seen: set[str] = set()
        for bucket in body.planned_buckets:
            wbs_list = sorted(w for w in bucket.wbs if w not in bucket_wbs_seen)
            bucket_wbs_seen.update(wbs_list)
            labeled_buckets_map[bucket.label] = list(wbs_list)
        for wbs in body.in_progress_wbs:
            orig = existing_bucket_for_wbs.get(wbs)
            if orig:
                bl = labeled_buckets_map.setdefault(orig, [])
                if wbs not in bl:
                    bl.append(wbs)
        labeled_buckets: list[VersionBucket] = [
            VersionBucket(label=lbl, items=sorted(items))
            for lbl, items in labeled_buckets_map.items()
        ]
        all_bucket_wbs = {w for items in labeled_buckets_map.values() for w in items}
        bucket_sa_used = {wbs_to_prefix[w] for w in all_bucket_wbs if w in wbs_to_prefix}
        unassigned_pl: set[str] = {wbs_to_prefix[w] for w in body.planned_wbs if w in wbs_to_prefix} - bucket_sa_used

        about_text = about_parser.transform_update_roadmap(about_text, RoadmapUpdate(
            in_progress=[sa_label.get(p, p) for p in sorted(ip_prefixes)],
            planned=[sa_label.get(p, p) for p in sorted(unassigned_pl)],
            planned_buckets=labeled_buckets,
            backlog=body.freeform_backlog,
        ))
        s.set_file("ABOUT.MD", about_text)
    else:
        prod = product_parser.parse(settings.product_md)
        wbs_to_feature = {f.wbs: f for area in prod.wbs_areas for sa in area.sub_areas for f in sa.features}
        wbs_to_prefix  = {f.wbs: sa.wbs_prefix for area in prod.wbs_areas for sa in area.sub_areas for f in sa.features}

        for wbs, f in wbs_to_feature.items():
            if f.status == FeatureStatus.planned and wbs not in all_active:
                try:
                    product_parser.update_feature_status(settings.product_md, wbs, FeatureStatus.idea)
                except Exception:
                    pass
        _promotable = {FeatureStatus.gap, FeatureStatus.idea}
        for wbs in all_active:
            f = wbs_to_feature.get(wbs)
            if f and f.status in _promotable:
                try:
                    product_parser.update_feature_status(settings.product_md, wbs, FeatureStatus.planned)
                except Exception:
                    pass

        prod2 = product_parser.parse(settings.product_md)
        sa_label = {sa.wbs_prefix: f"{sa.wbs_prefix} {sa.title}" for area in prod2.wbs_areas for sa in area.sub_areas}
        ip_prefixes = {wbs_to_prefix[w] for w in body.in_progress_wbs if w in wbs_to_prefix}

        pre_about_text = settings.about_md.read_text(encoding="utf-8")
        pre_pl = about_parser._parse_text(pre_about_text).roadmap_section("Planned")
        existing_bucket_for_wbs = {
            w: b.label
            for b in (pre_pl.buckets if pre_pl else [])
            for w in b.items
        }

        labeled_buckets_map: dict[str, list[str]] = {}
        bucket_wbs_seen: set[str] = set()
        for bucket in body.planned_buckets:
            wbs_list = sorted(w for w in bucket.wbs if w not in bucket_wbs_seen)
            bucket_wbs_seen.update(wbs_list)
            labeled_buckets_map[bucket.label] = list(wbs_list)
        for wbs in body.in_progress_wbs:
            orig = existing_bucket_for_wbs.get(wbs)
            if orig:
                bl = labeled_buckets_map.setdefault(orig, [])
                if wbs not in bl:
                    bl.append(wbs)
        labeled_buckets = [
            VersionBucket(label=lbl, items=sorted(items))
            for lbl, items in labeled_buckets_map.items()
        ]
        all_bucket_wbs = {w for items in labeled_buckets_map.values() for w in items}
        bucket_sa_used = {wbs_to_prefix[w] for w in all_bucket_wbs if w in wbs_to_prefix}
        unassigned_pl = {wbs_to_prefix[w] for w in body.planned_wbs if w in wbs_to_prefix} - bucket_sa_used

        about_parser.update_roadmap(settings.about_md, RoadmapUpdate(
            in_progress=[sa_label.get(p, p) for p in sorted(ip_prefixes)],
            planned=[sa_label.get(p, p) for p in sorted(unassigned_pl)],
            planned_buckets=labeled_buckets,
            backlog=body.freeform_backlog,
        ))
    return {"ok": True}


@router.post("/releases")
def post_release(body: NewRelease, request: Request):
    s = _session(request)
    if s:
        about_text = s.get_file("ABOUT.MD")
        about = about_parser._parse_text(about_text)
        in_progress_section = about.roadmap_section("In Progress")
        in_progress_items = in_progress_section.items if in_progress_section else []
        about_text = about_parser.transform_add_changelog_entry(about_text, body, in_progress_items)
        about_text = about_parser.transform_clear_version_buckets(about_text, body.version)
        s.set_file("ABOUT.MD", about_text)
    else:
        lock = about_parser._lock_for(settings.about_md)
        with lock:
            text = settings.about_md.read_text(encoding="utf-8")
            about = about_parser._parse_text(text)
            in_progress_section = about.roadmap_section("In Progress")
            in_progress_items = in_progress_section.items if in_progress_section else []
            text = about_parser.transform_add_changelog_entry(text, body, in_progress_items)
            text = about_parser.transform_clear_version_buckets(text, body.version)
            about_parser._atomic_write(settings.about_md, text)
    return {"ok": True, "version": body.version}


# ── WBS Chart ─────────────────────────────────────────────────────────────────

@router.post("/wbs/regenerate")
def regenerate_wbs(request: Request):
    s = _session(request)
    if s:
        # Write session files to a temp dir, run the script, clean up
        import tempfile as _tmp, shutil
        tmp_dir = Path(_tmp.mkdtemp(prefix="pac_wbs_"))
        try:
            for fname, content in s.files.items():
                (tmp_dir / fname).write_text(content, encoding="utf-8")
            result = wbs_module.regenerate(tmp_dir)
        except (FileNotFoundError, RuntimeError) as e:
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return result
    else:
        try:
            result = wbs_module.regenerate(settings.project_dir)
        except (FileNotFoundError, RuntimeError) as e:
            raise HTTPException(status_code=500, detail=str(e))
        return result


# ── Project switching (disk-mode only) ───────────────────────────────────────

class SwitchProjectBody(BaseModel):
    project_dir: str

@router.get("/browse")
def browse_directory(path: str = ""):
    import os
    target = Path(path).expanduser().resolve() if path else Path.home()
    try:
        dirs = []
        for entry in sorted(target.iterdir(), key=lambda e: e.name.lower()):
            if entry.name.startswith(".") or not entry.is_dir():
                continue
            try:
                entry.stat()
            except PermissionError:
                continue
            dirs.append({
                "name": entry.name,
                "path": str(entry),
                "is_project": (entry / "PRODUCT.MD").exists(),
            })
        parent = str(target.parent) if target != target.parent else None
        return {"path": str(target), "parent": parent, "dirs": dirs}
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Path not found")


@router.post("/switch-project")
def switch_project_api(body: SwitchProjectBody):
    if settings.lock_project:
        raise HTTPException(status_code=403, detail="Project switching is disabled on this instance.")
    from ..config import switch_project as _switch
    try:
        _switch(Path(body.project_dir))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "project_dir": str(settings.project_dir), "title": settings.app_title}


@router.get("/project/download")
def download_project(request: Request):
    s = _session(request)
    if s:
        candidates = {k: v for k, v in s.files.items() if k.endswith(".MD")}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, content in candidates.items():
                zf.writestr(name, content)
        buf.seek(0)
        safe_title = "".join(c if c.isalnum() or c in "-_" else "_" for c in s.title)
        filename = f"{safe_title or 'project'}_project.zip"
    else:
        candidates = {
            "PRODUCT.MD": settings.product_md,
            "ABOUT.MD":   settings.about_md,
            "README.MD":  settings.readme_md,
            "BUGS.MD":    settings.bugs_md,
        }
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, path in candidates.items():
                if path.exists():
                    zf.write(path, name)
        buf.seek(0)
        safe_title = "".join(c if c.isalnum() or c in "-_" else "_" for c in settings.app_title)
        filename = f"{safe_title}_project.zip"

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/project/upload")
async def upload_project(files: list[UploadFile] = File(...)):
    for f in files:
        if not (f.filename or "").lower().endswith(".md"):
            raise HTTPException(status_code=400, detail=f"Only .md files are accepted, got: {f.filename}")

    file_contents: dict[str, str] = {}
    for f in files:
        content = await f.read()
        filename = Path(f.filename).name.upper()
        file_contents[filename] = content.decode("utf-8")

    if "PRODUCT.MD" not in file_contents:
        raise HTTPException(status_code=400, detail="PRODUCT.MD is required.")

    text = file_contents["PRODUCT.MD"]
    m = re.match(r"^# (.+)", text)
    raw = m.group(1).strip() if m else "Project"
    title = re.sub(r"\s*[-–—]\s*(Product\s+)?Overview\s*$", "", raw, flags=re.IGNORECASE).strip()

    sid = session_store.create(file_contents, title=title)
    return {"ok": True, "title": title, "session_id": sid}


# ── Markdown preview ─────────────────────────────────────────────────────────

class MarkdownPreviewBody(BaseModel):
    content: str


@router.post("/markdown/preview")
def markdown_preview(body: MarkdownPreviewBody):
    import markdown as md
    html = md.markdown(body.content, extensions=["tables", "fenced_code"])
    return {"html": html}


# ── Product structure editing ─────────────────────────────────────────────────

class SectionUpdate(BaseModel):
    content: str


@router.put("/structure/users")
def put_users(body: SectionUpdate, request: Request):
    s = _session(request)
    try:
        if s:
            s.set_file("PRODUCT.MD", product_parser.transform_users(s.get_file("PRODUCT.MD"), body.content))
        else:
            product_parser.update_users(settings.product_md, body.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.put("/structure/workflows")
def put_workflows(body: SectionUpdate, request: Request):
    s = _session(request)
    try:
        if s:
            s.set_file("PRODUCT.MD", product_parser.transform_workflows(s.get_file("PRODUCT.MD"), body.content))
        else:
            product_parser.update_workflows(settings.product_md, body.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.put("/structure/scope")
def put_scope(body: SectionUpdate, request: Request):
    s = _session(request)
    try:
        if s:
            s.set_file("PRODUCT.MD", product_parser.transform_scope(s.get_file("PRODUCT.MD"), body.content))
        else:
            product_parser.update_scope(settings.product_md, body.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


# ── Bug screenshots ───────────────────────────────────────────────────────────

# Session-mode screenshots (in-memory, keyed by session_id → bug_id)
_session_screenshots: dict[str, dict[int, tuple[bytes, str]]] = {}

_SCREENSHOT_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp",
}


def _screenshots_dir() -> Path:
    d = settings.project_dir / ".screenshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.post("/bugs/{bug_id}/screenshot")
async def upload_bug_screenshot(bug_id: int, request: Request, file: UploadFile = File(...)):
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are accepted")
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Screenshot must be under 10 MB")
    content_type = file.content_type or "image/png"
    ext = Path(file.filename or "screenshot.png").suffix.lower() or ".png"
    s = _session(request)
    if s:
        _session_screenshots.setdefault(s.session_id, {})[bug_id] = (data, content_type)
    else:
        d = _screenshots_dir()
        for old in d.glob(f"bug_{bug_id}.*"):
            old.unlink(missing_ok=True)
        (d / f"bug_{bug_id}{ext}").write_bytes(data)
    return {"ok": True}


@router.get("/bugs/{bug_id}/screenshot")
def get_bug_screenshot(bug_id: int, request: Request, sid: str | None = None):
    # img tags can't send custom headers, so session_id is also accepted as a query param
    s = _session(request) or (session_store.get(sid) if sid else None)
    if s:
        entry = _session_screenshots.get(s.session_id, {}).get(bug_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="No screenshot")
        data, content_type = entry
        return Response(content=data, media_type=content_type)
    matches = list(_screenshots_dir().glob(f"bug_{bug_id}.*"))
    if not matches:
        raise HTTPException(status_code=404, detail="No screenshot")
    path = matches[0]
    return Response(
        content=path.read_bytes(),
        media_type=_SCREENSHOT_MIME.get(path.suffix.lower(), "image/png"),
    )


# ── Bug tracking ─────────────────────────────────────────────────────────────

@router.get("/bugs")
def get_bugs(request: Request):
    s = _session(request)
    if s:
        return bugs_parser._parse_text(s.get_file("BUGS.MD") or _EMPTY_BUGS)
    return bugs_parser.parse(settings.bugs_md)


@router.post("/bugs")
def post_bug(body: BugCreate, request: Request):
    s = _session(request)
    if s:
        text = s.get_file("BUGS.MD") or _EMPTY_BUGS
        text, bug = bugs_parser.transform_add_bug(text, body)
        s.set_file("BUGS.MD", text)
        return bug
    return bugs_parser.add_bug(settings.bugs_md, body)


@router.patch("/bugs/{bug_id}")
def patch_bug(bug_id: int, body: BugUpdate, request: Request):
    s = _session(request)
    try:
        if s:
            text = s.get_file("BUGS.MD") or _EMPTY_BUGS
            text, bug = bugs_parser.transform_update_bug(text, bug_id, body)
            s.set_file("BUGS.MD", text)
            return bug
        return bugs_parser.update_bug(settings.bugs_md, bug_id, body)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/bugs/{bug_id}/resolve")
def resolve_bug(bug_id: int, request: Request, body: dict = {}):
    resolved_in = body.get("resolved_in", "") if isinstance(body, dict) else ""
    s = _session(request)
    try:
        if s:
            text = s.get_file("BUGS.MD") or _EMPTY_BUGS
            s.set_file("BUGS.MD", bugs_parser.transform_resolve_bug(text, bug_id, resolved_in))
        else:
            bugs_parser.resolve_bug(settings.bugs_md, bug_id, resolved_in)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    # Delete screenshot now that the bug is resolved
    if s:
        _session_screenshots.get(s.session_id, {}).pop(bug_id, None)
    else:
        for old in _screenshots_dir().glob(f"bug_{bug_id}.*"):
            old.unlink(missing_ok=True)
    return {"ok": True}


# ── Live reload (SSE) ────────────────────────────────────────────────────────

@router.get("/events")
async def events(request: Request):
    s = _session(request)

    async def stream():
        if s:
            return  # session files are in-memory; nothing to watch
        watch_paths = [
            str(p) for p in [
                settings.product_md,
                settings.about_md,
                settings.readme_md,
                settings.bugs_md,
            ] if p.exists()
        ]
        if not watch_paths:
            return
        async for _ in awatch(*watch_paths):
            yield "data: changed\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

