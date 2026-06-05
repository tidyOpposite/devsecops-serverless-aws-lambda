"""DevSecOps Pipeline Kit CLI package."""

from . import main
from .main import VERSION

__version__ = VERSION

__all__ = ["VERSION", "__version__", "main"]
