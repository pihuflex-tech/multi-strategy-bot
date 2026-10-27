"""
routes.py
UI route handlers and HTML template renderer for the Quant Dashboard.

Phase 3B/3C: Responsive Flask Trading Dashboard UI:
- Serves read-only GET / route rendering index.html.
- Completely offline, self-contained; zero external CDNs or remote dependencies.
- Consumes /api/state for polling telemetry data.
"""

from typing import Any
from flask import Blueprint, render_template

ui_bp = Blueprint(
    "dashboard_ui",
    __name__,
    template_folder="templates",
)


@ui_bp.route("/", methods=["GET"])
def index() -> str:
    """Render the single-page dark-themed monitoring dashboard."""
    return render_template("index.html")


def attach_ui(app: Any) -> Any:
    """Attach the UI blueprint to an existing Flask application instance."""
    if "dashboard_ui" not in app.blueprints:
        app.register_blueprint(ui_bp)
    return app


class RoutesPlaceholder:
    """Backward compatibility alias for routes."""
    blueprint = ui_bp
