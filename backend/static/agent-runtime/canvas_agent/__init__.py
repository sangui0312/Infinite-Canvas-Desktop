"""Durable, locally scoped canvas assistant. No provider credentials are stored here."""

from .service import AgentService
from .router import install_agent

__all__ = ["AgentService", "install_agent"]
