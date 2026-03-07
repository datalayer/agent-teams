# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""FastAPI routes for Agent Teams."""

from .events import router as events_router
from .teams import router as teams_router

__all__ = [
    "events_router",
    "teams_router",
]
