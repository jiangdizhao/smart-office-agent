"""RTX Vision Edge Server package."""

from app.visit_session_isolation import install_visit_session_isolation

install_visit_session_isolation()

__all__ = ["__version__"]
__version__ = "0.7.0"
