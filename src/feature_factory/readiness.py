"""Feature readiness policy helpers.

This module intentionally does not build expanded-data features. It exposes the
readiness catalog used by research_data_readiness so feature_factory callers can
see which raw datasets are safe candidates for future v3 feature families.
"""

from __future__ import annotations

from research_data_readiness.feature_readiness import FEATURE_FAMILY_POLICIES, build_feature_readiness_catalog

__all__ = ["FEATURE_FAMILY_POLICIES", "build_feature_readiness_catalog"]
