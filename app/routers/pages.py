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

router = APIRouter()

_EMPTY_BUGS = bugs_parser._EMPTY_BUGS


def _get_session(request: Request) -> session_store.Session | None:
    return session_store.get(request.headers.get("X-Session-ID"))


def _ctx(request: Request, active_page: str, **kwargs) -> dict:
    s = _get_session(request)
    if s:
        return {"active_page": active_page, "app_title": s.title, "is_uploaded": True, **kwargs}
    return {"active_page": active_page, "app_title": settings.app_title, "is_uploaded": settings.is_uploaded, **kwargs}


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
    s = _get_session(request)
    if s:
        product = product_parser._parse_text(s.get_file("PRODUCT.MD"))
    else:
        product = product_parser.parse(settings.product_md)
    return templates.TemplateResponse(request, "structure.html", _ctx(request, "structure", product=product))


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
        product = product_parser._parse_text(s.get_file("PRODUCT.MD"))
    else:
        product = product_parser.parse(settings.product_md)

    all_features = product.all_features
    sub_areas    = [sa for area in product.wbs_areas for sa in area.sub_areas]

    ideas       = [f for f in all_features if f.status in (FeatureStatus.gap, FeatureStatus.idea)]
    scoped      = [f for f in all_features if f.status == FeatureStatus.scoped]
    scored      = sorted(
        [f for f in all_features if f.status == FeatureStatus.scored],
        key=lambda f: f.priority_score or 0,
        reverse=True,
    )
    in_progress = [f for f in all_features if f.status in (FeatureStatus.in_progress, FeatureStatus.planned)]
    completed   = [f for f in all_features if f.status == FeatureStatus.live]

    return templates.TemplateResponse(request, "features.html", _ctx(
        request, "features",
        product=product,
        ideas=ideas,
        scoped=scoped,
        scored=scored,
        in_progress=in_progress,
        completed=completed,
        sub_areas=sub_areas,
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

    defining_sub_areas = []
    for area in product.wbs_areas:
        for sa in area.sub_areas:
            unscoped = [f for f in sa.features if f.status in (FeatureStatus.gap, FeatureStatus.idea)]
            if unscoped:
                defining_sub_areas.append({
                    "prefix": sa.wbs_prefix,
                    "title":  sa.title,
                    "gap_count": len(unscoped),
                    "sa": sa,
                })

    ip_section = about.roadmap_section("In Progress")
    pl_section = about.roadmap_section("Planned")
    ip_prefixes = {item.split(" ", 1)[0] for item in (ip_section.items if ip_section else [])}
    pl_prefixes = {item.split(" ", 1)[0] for item in (pl_section.items if pl_section else [])}

    in_progress_features: list[dict] = []
    planned_features: list[dict] = []
    backlog_features: list[dict] = []

    _active = (FeatureStatus.in_progress, FeatureStatus.planned)
    _unstarted = (FeatureStatus.gap, FeatureStatus.idea, FeatureStatus.scoped, FeatureStatus.scored)

    for area in product.wbs_areas:
        for sa in area.sub_areas:
            for feat in sa.features:
                if feat.status == FeatureStatus.live:
                    continue
                entry = {"f": feat, "sa_prefix": sa.wbs_prefix, "sa_title": sa.title}
                if feat.status in _active and sa.wbs_prefix in ip_prefixes:
                    in_progress_features.append(entry)
                elif feat.status in _active and sa.wbs_prefix in pl_prefixes:
                    planned_features.append(entry)
                elif feat.status in _active:
                    in_progress_features.append(entry)
                elif feat.status in _unstarted:
                    backlog_features.append(entry)

    bl_section = about.roadmap_section("Backlog")
    freeform_backlog = bl_section.items if bl_section else []
    next_ver   = next_release_version(about, product)
    ver_reason = version_rationale(about)

    return templates.TemplateResponse(request, "roadmap.html", _ctx(
        request, "roadmap",
        product=product,
        about=about,
        sub_area_map=sub_area_map,
        defining_sub_areas=defining_sub_areas,
        in_progress_features=in_progress_features,
        planned_features=planned_features,
        backlog_features=backlog_features,
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
