import json
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import settings
from .. import session_store
from ..models import FeatureStatus, BugStatus
from ..parsers import product as product_parser
from ..parsers import about as about_parser
from ..parsers import readme as readme_parser
from ..parsers import bugs as bugs_parser
from ..template_env import templates
from ..versioning import next_release_version, version_rationale
from ..auth import enabled as auth_enabled

router = APIRouter()

_EMPTY_BUGS = bugs_parser._EMPTY_BUGS


def _get_session(request: Request) -> session_store.Session | None:
    return session_store.get(request.headers.get("X-Session-ID"))


def _ctx(request: Request, active_page: str, **kwargs) -> dict:
    s = _get_session(request)
    base = {"active_page": active_page, "auth_enabled": auth_enabled()}
    if s:
        return {**base, "app_title": s.title, "is_uploaded": True, **kwargs}
    return {**base, "app_title": settings.app_title, "is_uploaded": settings.is_uploaded, **kwargs}


def _parse_product_about(s: session_store.Session):
    product = product_parser._parse_text(s.get_file("PRODUCT.MD"))
    about   = about_parser._parse_text(s.get_file("ABOUT.MD"))
    return product, about


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    s = _get_session(request)
    if s:
        product, about = _parse_product_about(s)
    else:
        product = product_parser.parse(settings.product_md)
        about   = about_parser.parse(settings.about_md)

    all_features = product.all_features
    ideas       = [f for f in all_features if f.status in (FeatureStatus.gap, FeatureStatus.idea)]
    scoped      = [f for f in all_features if f.status == FeatureStatus.scoped]
    scored      = [f for f in all_features if f.status == FeatureStatus.scored]
    in_progress = [f for f in all_features if f.status in (FeatureStatus.in_progress, FeatureStatus.planned)]

    return templates.TemplateResponse(request, "dashboard.html", _ctx(
        request, "dashboard",
        product=product,
        about=about,
        roadmap_in_progress=about.roadmap_section("In Progress"),
        recent_changelog=about.changelog[:3],
        ideas=ideas,
        scoped=scoped,
        scored=scored,
        in_progress_items=in_progress,
        sub_areas=[sa for area in product.wbs_areas for sa in area.sub_areas],
    ))


@router.get("/structure", response_class=HTMLResponse)
def structure_page(request: Request):
    import re as _re
    s = _get_session(request)
    if s:
        product = product_parser._parse_text(s.get_file("PRODUCT.MD"))
    else:
        product = product_parser.parse(settings.product_md)

    workflows_data = []
    for m in _re.finditer(r"### ([^\n]+)\n(.*?)(?=\n### |\Z)", product.workflows_md, _re.DOTALL):
        name = m.group(1).strip()
        persona = ""
        desc_lines = []
        for line in m.group(2).strip().splitlines():
            if line.strip().lower().startswith("persona:"):
                persona = line.strip()[len("persona:"):].strip()
            else:
                desc_lines.append(line)
        description = "\n".join(desc_lines).strip()
        workflows_data.append({"persona": persona, "name": name, "description": description})

    users_json = json.dumps([
        {"name": u.name, "description": u.description} for u in product.users
    ])
    workflows_json = json.dumps(workflows_data)
    scope_json = json.dumps([
        {"title": g.title, "items": [{"text": i.text, "complete": i.complete} for i in g.items]}
        for g in product.scope_groups
    ])

    return templates.TemplateResponse(request, "structure.html", _ctx(
        request, "structure",
        product=product,
        users_json=users_json,
        workflows_json=workflows_json,
        scope_json=scope_json,
    ))


@router.get("/registry", response_class=HTMLResponse)
def registry_page(request: Request):
    s = _get_session(request)
    if s:
        product = product_parser._parse_text(s.get_file("PRODUCT.MD"))
    else:
        product = product_parser.parse(settings.product_md)
    return templates.TemplateResponse(request, "product.html", _ctx(request, "registry", product=product))


@router.get("/product", response_class=HTMLResponse)
def product_redirect(request: Request):
    return RedirectResponse(url="/registry")


@router.get("/features", response_class=HTMLResponse)
def features_page(request: Request):
    s = _get_session(request)
    if s:
        product, about = _parse_product_about(s)
    else:
        product = product_parser.parse(settings.product_md)
        about   = about_parser.parse(settings.about_md)

    all_features = product.all_features
    sub_areas    = [sa for area in product.wbs_areas for sa in area.sub_areas]

    # Sub-areas that belong to the most recent completed release and the in-progress release
    review_prefixes: set[str] = set()
    found_completed = False
    for entry in about.changelog:
        if entry.in_progress or not found_completed:
            for group in entry.groups:
                review_prefixes.add(group.label.split(" ", 1)[0])
            if not entry.in_progress:
                found_completed = True

    ideas       = [f for f in all_features if f.status in (FeatureStatus.gap, FeatureStatus.idea)]
    prioritized = sorted(
        [f for f in all_features if f.status in (FeatureStatus.scoped, FeatureStatus.scored)],
        key=lambda f: (-(f.priority_score or 0), 0 if f.status == FeatureStatus.scored else 1),
    )
    in_progress = [f for f in all_features if f.status in (FeatureStatus.in_progress, FeatureStatus.planned)]
    review      = [
        f for f in all_features
        if f.status == FeatureStatus.live
        and any(f.wbs.startswith(p + ".") for p in review_prefixes)
    ]
    released    = [f for f in all_features if f.status == FeatureStatus.released]

    features_json = json.dumps([
        {"wbs": f.wbs, "name": f.name, "status": f.status.value,
         "notes": f.notes or "", "value": f.value, "effort": f.effort}
        for f in all_features
    ])

    return templates.TemplateResponse(request, "features.html", _ctx(
        request, "features",
        product=product,
        ideas=ideas,
        prioritized=prioritized,
        in_progress=in_progress,
        review=review,
        released=released,
        sub_areas=sub_areas,
        features_json=features_json,
    ))


@router.get("/backlog", response_class=HTMLResponse)
def backlog_redirect(request: Request):
    return RedirectResponse(url="/features")


@router.get("/roadmap", response_class=HTMLResponse)
def roadmap_page(request: Request):
    s = _get_session(request)
    if s:
        product, about = _parse_product_about(s)
    else:
        about   = about_parser.parse(settings.about_md)
        product = product_parser.parse(settings.product_md)

    sub_area_map = {sa.wbs_prefix: sa for area in product.wbs_areas for sa in area.sub_areas}

    ip_section = about.roadmap_section("In Progress")
    pl_section = about.roadmap_section("Planned")
    ip_prefixes = {item.split(" ", 1)[0] for item in (ip_section.items if ip_section else [])}

    # pl_prefixes includes items from all version buckets AND flat/unassigned planned items
    pl_all_items: list[str] = []
    if pl_section:
        pl_all_items.extend(pl_section.items)
        for bucket in pl_section.buckets:
            pl_all_items.extend(bucket.items)
    pl_prefixes = {item.split(" ", 1)[0] for item in pl_all_items}

    in_progress_features: list[dict] = []
    planned_features: list[dict] = []
    backlog_features: list[dict] = []
    next_release_features: list[dict] = []

    _active    = (FeatureStatus.in_progress, FeatureStatus.planned)
    _unstarted = (FeatureStatus.gap, FeatureStatus.idea, FeatureStatus.scoped, FeatureStatus.scored)
    _active_prefixes = ip_prefixes | pl_prefixes

    # Include Live features from any sub-area named in the in-progress changelog entry.
    # These shipped as part of the pending release but are no longer in the roadmap sections.
    ip_changelog = next((e for e in about.changelog if e.in_progress), None)
    ip_changelog_sa_prefixes: set[str] = set()
    if ip_changelog:
        for group in ip_changelog.groups:
            prefix = group.label.split(" ", 1)[0]
            ip_changelog_sa_prefixes.add(prefix)
    _next_release_prefixes = _active_prefixes | ip_changelog_sa_prefixes

    for area in product.wbs_areas:
        for sa in area.sub_areas:
            for feat in sa.features:
                entry = {"f": feat, "sa_prefix": sa.wbs_prefix, "sa_title": sa.title}
                if feat.status == FeatureStatus.live:
                    if sa.wbs_prefix in _next_release_prefixes:
                        next_release_features.append(entry)
                    continue
                if feat.status in _active and sa.wbs_prefix in ip_prefixes:
                    in_progress_features.append(entry)
                elif feat.status in _active and sa.wbs_prefix in pl_prefixes:
                    planned_features.append(entry)
                elif feat.status in _active:
                    in_progress_features.append(entry)
                elif feat.status in _unstarted:
                    backlog_features.append(entry)

    # Build version bucket display data: group planned_features by bucket
    sa_to_bucket: dict[str, str] = {}
    if pl_section:
        for bucket in pl_section.buckets:
            for item in bucket.items:
                prefix = item.split(" ", 1)[0]
                sa_to_bucket[prefix] = bucket.label

    version_buckets: list[dict] = []
    if pl_section:
        for bucket in pl_section.buckets:
            bucket_sa = {item.split(" ", 1)[0] for item in bucket.items}
            version_buckets.append({
                "label":    bucket.label,
                "label_id": bucket.label.lower().replace(" ", "-").replace(".", "-"),
                "features": [e for e in planned_features if e["sa_prefix"] in bucket_sa],
            })

    unassigned_planned = [e for e in planned_features if e["sa_prefix"] not in sa_to_bucket]

    # Sort backlog: descending priority score, then by status (Scored > Scoped > Idea > Gap)
    _status_rank = {
        FeatureStatus.scored: 0,
        FeatureStatus.scoped: 1,
        FeatureStatus.idea:   2,
        FeatureStatus.gap:    3,
    }
    backlog_sorted = sorted(
        backlog_features,
        key=lambda e: (-(e["f"].priority_score or 0), _status_rank.get(e["f"].status, 99)),
    )

    bl_section = about.roadmap_section("Backlog")
    freeform_backlog = bl_section.items if bl_section else []
    next_ver   = next_release_version(about, product)
    ver_reason = version_rationale(about)

    return templates.TemplateResponse(request, "roadmap.html", _ctx(
        request, "roadmap",
        product=product,
        about=about,
        sub_area_map=sub_area_map,
        in_progress_features=in_progress_features,
        next_release_features=next_release_features,
        version_buckets=version_buckets,
        unassigned_planned=unassigned_planned,
        backlog_sorted=backlog_sorted,
        freeform_backlog=freeform_backlog,
        next_version=next_ver,
        version_rationale=ver_reason,
        sub_areas=[sa for area in product.wbs_areas for sa in area.sub_areas],
    ))


@router.get("/readme", response_class=HTMLResponse)
def readme_page(request: Request):
    s = _get_session(request)
    if s:
        import markdown as md
        content = md.markdown(s.get_file("README.MD") or "", extensions=["tables", "fenced_code"])
    else:
        content = readme_parser.render(settings.readme_md)
    return templates.TemplateResponse(request, "readme.html", _ctx(request, "readme", readme_html=content))


@router.get("/about", response_class=HTMLResponse)
def about_page(request: Request):
    s = _get_session(request)
    import markdown as md
    if s:
        text = s.get_file("ABOUT.MD") or ""
    else:
        text = settings.about_md.read_text(encoding="utf-8") if settings.about_md.exists() else ""
    content = md.markdown(text, extensions=["tables", "fenced_code", "nl2br"])
    return templates.TemplateResponse(request, "about.html", _ctx(request, "about", about_html=content))


@router.get("/bugs", response_class=HTMLResponse)
def bugs_page(request: Request):
    s = _get_session(request)
    if s:
        doc     = bugs_parser._parse_text(s.get_file("BUGS.MD") or _EMPTY_BUGS)
        product = product_parser._parse_text(s.get_file("PRODUCT.MD"))
    else:
        doc     = bugs_parser.parse(settings.bugs_md)
        product = product_parser.parse(settings.product_md)

    sub_areas = [sa for area in product.wbs_areas for sa in area.sub_areas]
    open_bugs          = [b for b in doc.active if b.status == BugStatus.open]
    investigating_bugs = [b for b in doc.active if b.status == BugStatus.investigating]
    fixing_bugs        = [b for b in doc.active if b.status == BugStatus.fix_in_progress]

    return templates.TemplateResponse(request, "bugs.html", _ctx(
        request, "bugs",
        doc=doc,
        open_bugs=open_bugs,
        investigating_bugs=investigating_bugs,
        fixing_bugs=fixing_bugs,
        sub_areas=sub_areas,
    ))


@router.get("/switch-project", response_class=HTMLResponse)
def switch_project_page(request: Request):
    import re as _re
    project_title = settings.app_title
    try:
        text = settings.product_md.read_text(encoding="utf-8")
        m = _re.match(r"^# (.+)", text)
        if m:
            raw = m.group(1).strip()
            project_title = _re.sub(r"\s*[-–]\s*(Product\s+)?Overview\s*$", "", raw, flags=_re.IGNORECASE).strip()
    except Exception:
        pass
    return templates.TemplateResponse(request, "switch_project.html", _ctx(
        request, "switch",
        project_title=project_title,
        source_path=settings.source_path if not settings.is_uploaded else None,
        recent_projects=settings.recent_projects,
    ))
