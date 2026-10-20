"""paper2youtube: arXiv/PDF → notes + slides + per-figure audio + YouTube video."""

__version__ = "0.1.0"

from .config import Settings, load_settings
from .pipeline import run_pipeline

__all__ = ["Settings", "load_settings", "run_pipeline", "__version__"]