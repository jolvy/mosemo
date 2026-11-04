"""What other bounded contexts may use from labels (ADR 0011)."""

from mosemo.labels.application.catalog import ActiveLabel, LabelCatalog
from mosemo.labels.presentation.errors import LabelErrorCode

__all__ = ["ActiveLabel", "LabelCatalog", "LabelErrorCode"]
