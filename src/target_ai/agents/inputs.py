"""Входные данные агентов: агенты получают всё аргументами, базы и Meta не видят."""

from collections.abc import Mapping
from typing import Any

from pydantic import Field

from target_ai.money import Money

from .schemas import AgentModel, Objective, Passport, PassportFact, SpecialCategory, Voice


class Questionnaire(AgentModel):
    company_name: str
    instagram_handle: str | None = None
    website: str | None = None
    geo: str | None = None
    objective: Objective | None = None
    offer_note: str | None = None
    notes: str | None = None


class Screenshot(AgentModel):
    media_type: str = "image/png"
    data: bytes


class AnalystInput(AgentModel):
    questionnaire: Questionnaire
    ig_profile: Mapping[str, Any] | None = None
    ig_posts: tuple[Mapping[str, Any], ...] = ()
    screenshots: tuple[Screenshot, ...] = ()
    competitor_urls: tuple[str, ...] = ()
    history: tuple[str, ...] = ()
    user_facts: tuple[PassportFact, ...] = ()  # добавлены владельцем через бота — доверенные
    external_facts: tuple[PassportFact, ...] = ()  # найдены в источниках — недоверенные


class InterestOption(AgentModel):
    name: str
    audience_size: int | None = None


class StrategyInput(AgentModel):
    passport: Passport
    user_facts: tuple[PassportFact, ...] = ()
    external_facts: tuple[PassportFact, ...] = ()
    questionnaire: Questionnaire
    daily_cap: Money
    min_group_budget: Money
    interests: tuple[InterestOption, ...] = ()
    history: tuple[str, ...] = ()
    max_groups: int = Field(default=4, ge=1, le=4)
    retargeting_available: bool = False
    lookalike_available: bool = False


class CreativeOption(AgentModel):
    id: str
    kind: str = "post"
    caption: str = ""
    score: float | None = None


class CopyInput(AgentModel):
    """Копирайтер видит только факты и голос — не весь паспорт."""

    user_facts: tuple[PassportFact, ...] = ()
    external_facts: tuple[PassportFact, ...] = ()
    voice: Voice
    company_name: str
    objective: Objective
    hypothesis: str
    segment: str | None = None
    creatives: tuple[CreativeOption, ...] = ()
    feedback: str | None = None


class AdForReview(AgentModel):
    index: int
    kind: str
    text: str = ""
    headline: str = ""
    cta: str = ""


class ControlInput(AgentModel):
    ads: tuple[AdForReview, ...]
    targeting: Mapping[str, Any] = Field(default_factory=dict)
    user_facts: tuple[PassportFact, ...] = ()
    external_facts: tuple[PassportFact, ...] = ()
    language: str = "ru"
    category: SpecialCategory | None = None
    objective: Objective | None = None


class DecisionInfo(AgentModel):
    index: int
    kind: str
    target: str
    reason: str


class ExplainInput(AgentModel):
    company_name: str
    decisions: tuple[DecisionInfo, ...]
    stats: tuple[Mapping[str, Any], ...] = ()
    history: tuple[str, ...] = ()
    suggest: bool = True


class ReportInput(AgentModel):
    company_name: str
    period: str = "day"
    stats: tuple[Mapping[str, Any], ...] = ()
    changes: tuple[str, ...] = ()
    history: tuple[str, ...] = ()
    target_cpa: str | None = None


class IntentContext(AgentModel):
    dialog_state: str | None = None
    companies: tuple[str, ...] = ()
