"""Compatibility exports for resource discovery."""

from .gpu_probe import probe_compute_resources, write_resource_snapshot

__all__ = ["probe_compute_resources", "write_resource_snapshot"]
