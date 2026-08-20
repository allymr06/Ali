"""Public application entry point.

Application construction lives in :mod:`app.bootstrap`; this module remains
as the stable import path used by callers.
"""

from app.bootstrap import JARVISApplication, create_application

__all__ = ["JARVISApplication", "create_application"]
