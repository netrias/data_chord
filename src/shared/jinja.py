"""Shared Jinja template loading for stage pages."""

from pathlib import Path

from fastapi.templating import Jinja2Templates

_SHARED_TEMPLATE_DIR = Path(__file__).parent / "templates"


def templates_for_stage(stage_template_dir: Path) -> Jinja2Templates:
    """Load stage-owned templates before shared page components."""
    return Jinja2Templates(directory=[str(stage_template_dir), str(_SHARED_TEMPLATE_DIR)])
