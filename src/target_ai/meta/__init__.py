"""Клиент Graph API: единственное место HTTP-обращений к Meta (спека §6)."""

from target_ai.meta.client import DEFAULT_VERSION, MetaClient
from target_ai.meta.errors import MetaError
from target_ai.meta.gateway import MetaGateway
from target_ai.meta.models import AdAccount, TokenInfo

__all__ = ["DEFAULT_VERSION", "AdAccount", "MetaClient", "MetaError", "MetaGateway", "TokenInfo"]
