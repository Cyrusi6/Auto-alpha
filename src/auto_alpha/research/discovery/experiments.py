"""Alpha Factory campaign warehouse and factor-store consolidation."""

from auto_alpha.research.discovery.experiments_consolidate import consolidate_factor_stores
from auto_alpha.research.discovery.experiments_consolidate import discover_shard_factor_stores
from auto_alpha.research.discovery.experiments_ingest import ingest_alpha_factory_run
from auto_alpha.research.discovery.experiments_leaderboard import build_leaderboard
from auto_alpha.research.discovery.experiments_leaderboard import build_leaderboard_from_factor_store
from auto_alpha.research.discovery.experiments_leaderboard import load_candidate_pool
from auto_alpha.research.discovery.experiments_leaderboard import write_validation_candidate_pool
from auto_alpha.research.discovery.experiments_models import AlphaConsolidatedFactorRecord
from auto_alpha.research.discovery.experiments_models import AlphaExperimentRecord
from auto_alpha.research.discovery.experiments_models import AlphaLeaderboardRecord
from auto_alpha.research.discovery.experiments_models import AlphaShardRecord
from auto_alpha.research.discovery.experiments_registry import LocalAlphaExperimentStore

__all__ = [
    "AlphaConsolidatedFactorRecord",
    "AlphaExperimentRecord",
    "AlphaLeaderboardRecord",
    "AlphaShardRecord",
    "LocalAlphaExperimentStore",
    "build_leaderboard",
    "build_leaderboard_from_factor_store",
    "consolidate_factor_stores",
    "discover_shard_factor_stores",
    "ingest_alpha_factory_run",
    "load_candidate_pool",
    "write_validation_candidate_pool",
]
