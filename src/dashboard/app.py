"""
app.py
Flask web application factory and configuration.

Phase 3A: Minimal, offline Flask application factory:
- Provides create_app(telemetry_provider=None, config=None)
- Registers read-only REST API endpoints under /api
- Does not start the server on import
- Debug mode disabled by default
- No external CDN dependencies or new packages
"""

from typing import Any, Dict, Optional
from flask import Flask

from src.dashboard.api import api_bp
from src.dashboard.routes import ui_bp


def create_app(
    telemetry_provider: Any = None,
    config: Optional[Dict[str, Any]] = None,
) -> Flask:
    """
    Application factory for the dashboard service.
    
    Args:
        telemetry_provider: Injected telemetry source (callable, object, dict, path, or None).
        config: Optional Flask configuration dictionary.
        
    Returns:
        Configured Flask application instance with both API and UI blueprints.
    """
    app = Flask(__name__)
    
    # Sensible offline production defaults
    app.config["DEBUG"] = False
    app.config["TESTING"] = False
    app.config["JSON_SORT_KEYS"] = False
    
    # Apply custom configuration if provided
    if config:
        app.config.update(config)
        
    # Store telemetry provider in application config
    app.config["TELEMETRY_PROVIDER"] = telemetry_provider
    
    # Register read-only REST API and UI blueprints
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(ui_bp)
    
    return app


class AppPlaceholder:
    """Backward compatibility alias for dashboard app factory."""
    create_app = staticmethod(create_app)
