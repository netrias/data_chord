"""
Re-export shared dependency getters from domain for backward compatibility.

This module exists for backward compatibility. New code should import directly
from src.domain.dependencies.
"""

from __future__ import annotations

from src.domain.dependencies import MODULE_DIR as MODULE_DIR
from src.domain.dependencies import UPLOAD_BASE_DIR as UPLOAD_BASE_DIR
from src.domain.dependencies import get_file_store as get_file_store
from src.domain.dependencies import get_harmonize_service as get_harmonize_service
from src.domain.dependencies import get_mapping_service as get_mapping_service
from src.domain.dependencies import get_upload_constraints as get_upload_constraints
from src.domain.dependencies import get_upload_storage as get_upload_storage

__all__ = [
    "MODULE_DIR",
    "UPLOAD_BASE_DIR",
    "get_upload_constraints",
    "get_upload_storage",
    "get_mapping_service",
    "get_harmonize_service",
    "get_file_store",
]
