"""Matrix cache utilities for governed A-share datasets."""

from .builder import build_matrix_cache
from .models import MatrixCacheBuildResult, MatrixFieldInfo, MatrixValidationReport
from .reader import MatrixStoreReader
from .strict_engineering import (
    StrictEngineeringMatrixError,
    StrictEngineeringPITMatrixBuilder,
    StrictEngineeringPITMatrixConfig,
    StrictEngineeringPITMatrixResult,
)
from .validator import validate_matrix_cache

__all__ = [
    "MatrixCacheBuildResult",
    "MatrixFieldInfo",
    "MatrixStoreReader",
    "MatrixValidationReport",
    "StrictEngineeringMatrixError",
    "StrictEngineeringPITMatrixBuilder",
    "StrictEngineeringPITMatrixConfig",
    "StrictEngineeringPITMatrixResult",
    "build_matrix_cache",
    "validate_matrix_cache",
]
