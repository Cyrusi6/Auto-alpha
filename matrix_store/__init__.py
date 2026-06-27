"""Matrix cache utilities for governed A-share datasets."""

from .builder import build_matrix_cache
from .models import MatrixCacheBuildResult, MatrixFieldInfo, MatrixValidationReport
from .reader import MatrixStoreReader
from .validator import validate_matrix_cache

__all__ = [
    "MatrixCacheBuildResult",
    "MatrixFieldInfo",
    "MatrixStoreReader",
    "MatrixValidationReport",
    "build_matrix_cache",
    "validate_matrix_cache",
]
