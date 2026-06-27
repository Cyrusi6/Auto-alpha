"""A-share sample provider entry point."""

from .ashare.providers import AShareDataProvider, SampleAShareDataProvider, create_ashare_provider

__all__ = ["AShareDataProvider", "SampleAShareDataProvider", "create_ashare_provider"]
