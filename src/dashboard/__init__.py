"""
Web Dashboard Service.

Phase 3A: Minimal, offline Flask application and controllable service runner.
"""

from src.dashboard.app import create_app, AppPlaceholder
from src.dashboard.api import api_bp, ApiPlaceholder, sanitize_telemetry
from src.dashboard.dashboard_service import DashboardService, DashboardServicePlaceholder

__all__ = [
    "create_app",
    "AppPlaceholder",
    "api_bp",
    "ApiPlaceholder",
    "sanitize_telemetry",
    "DashboardService",
    "DashboardServicePlaceholder",
]
