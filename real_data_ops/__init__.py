"""Production-oriented real data operations for local A-share research."""

from .env_file import load_env_file, redacted_token_metadata
from .pipeline import run_real_data_pipeline
from .profiles import get_real_data_profile
from .size_report import compute_data_size_report

__all__ = [
    "compute_data_size_report",
    "get_real_data_profile",
    "load_env_file",
    "redacted_token_metadata",
    "run_real_data_pipeline",
]
