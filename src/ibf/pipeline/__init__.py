"""
Primary pipeline entry points.
"""

from .executor import PipelineRunError, execute_pipeline

__all__ = ["PipelineRunError", "execute_pipeline"]
