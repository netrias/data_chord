"""Shared Jinja template loading for stage pages."""

from pathlib import Path
from urllib.parse import urlencode

from fastapi import Request
from fastapi.templating import Jinja2Templates

from src.domain.dataset_workflow_ids import dataset_workflow_id_from_string

_SHARED_TEMPLATE_DIR = Path(__file__).parent / "templates"


def templates_for_stage(stage_template_dir: Path) -> Jinja2Templates:
    """Load stage-owned templates before shared page components."""
    templates = Jinja2Templates(directory=[str(stage_template_dir), str(_SHARED_TEMPLATE_DIR)])
    templates.env.globals["app_path_for"] = _app_path_for
    templates.env.globals["workflow_path_for"] = _workflow_path_for
    return templates


def _app_path_for(request: Request, route_name: str, **path_params: str) -> str:
    """Keep browser-owned routes on the visible origin behind a reverse proxy."""
    return request.url_for(route_name, **path_params).path


def _workflow_path_for(request: Request, route_name: str) -> str:
    """Build a stage path with only a validated workflow identity."""
    route_path = _app_path_for(request, route_name)
    raw_file_ids = request.query_params.getlist("file_id")
    if len(raw_file_ids) != 1:
        return route_path
    try:
        file_id = dataset_workflow_id_from_string(raw_file_ids[0])
    except ValueError:
        return route_path
    return f"{route_path}?{urlencode({'file_id': file_id})}"
