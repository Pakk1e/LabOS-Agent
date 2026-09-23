"""CI provider interfaces and local execution backend."""
from .base import CIProvider, CIResult, CICommandResult
from .local import LocalCI

__all__ = ["CIProvider", "CIResult", "CICommandResult", "LocalCI"]
