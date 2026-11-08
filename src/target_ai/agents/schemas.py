"""Схемы ответов агентов (спека §5). Без зависимостей от storage/meta/executor.

В схемах плана и подсказок нет полей для абсолютных сумм, ID объектов Meta,
валюты, лимита и режима: модель не может их задать, даже если очень захочет.
"""

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NOT_FOUND = "не найдено"
MIN_SEGMENTS, MAX_SEGMENTS = 2, 4
MIN_GROUPS, MAX_GROUPS = 2, 4
MIN_VARIANTS, MAX_VARIANTS = 2, 3


class AgentModel(BaseModel):
    """Неизменяемая модель; лишние поля из ответа молча отбрасываются."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class Objective(StrEnum):
    """Поддерживаемые цели (спека §6, таблица целей)."""

    DIRECT_MESSAGES = "direct_messages"
    LEAD_FORM = "lead_form"
    WEBSITE_TRAFFIC = "website_traffic"
    PROFILE_TRAFFIC = "profile_traffic"
    REACH = "reach"


class Hypothesis(StrEnum):
    BROAD = "broad"
    INTERESTS = "interests"
    RETARGETING = "retargeting"
    LOOKALIKE = "lookalike"


class SpecialCategory(StrEnum):
    """Особые категории рекламы Meta (спека §6, р.17)."""

    FINANCIAL_PRODUCTS_SERVICES = "FINANCIAL_PRODUCTS_SERVICES"
    HOUSING = "HOUSING"
    EMPLOYMENT = "EMPLOYMENT"
    ISSUES_ELECTIONS_POLITICS = "ISSUES_ELECTIONS_POLITICS"
    ONLINE_GAMBLING_AND_GAMING = "ONLINE_GAMBLING_AND_GAMING"


# --- Паспорт ---------------------------------------------------------------


class Segment(AgentModel):
    name: str
    pains: tuple[str, ...] = ()
    motives: tuple[str, ...] = ()


class Competitor(AgentModel):
    name: str
    url: str = NOT_FOUND
    difference: str = NOT_FOUND


class TopPost(AgentModel):
    media_id: str
    why: str


class Voice(AgentModel):
    language: str = Field(description="код языка текстов: ru, uz, en…")
    address: Literal["ты", "вы"]
    style: str
    phrases: tuple[str, ...] = ()


class PassportFact(AgentModel):
    text: str
    source: str = Field(min_length=1, description="откуда факт: сайт, пост, анкета…")


class Passport(AgentModel):
    offer: str = NOT_FOUND
    audience: str = NOT_FOUND
    usp: str = NOT_FOUND
    segments: tuple[Segment, ...] = Field(min_length=MIN_SEGMENTS, max_length=MAX_SEGMENTS)
    competitors: tuple[Competitor, ...] = ()
    top_posts: tuple[TopPost, ...] = ()
    recommended_objective: Objective
    recommended_monthly_budget: Decimal | None = None
    budget_rationale: str = NOT_FOUND
    expected_cpa: Decimal | None = None
    cpa_rationale: str = NOT_FOUND
    risks: tuple[str, ...] = ()
    voice: Voice
    facts: tuple[PassportFact, ...] = ()
    not_found: tuple[str, ...] = ()


# --- Медиаплан -------------------------------------------------------------


class GroupProposal(AgentModel):
    name: str
    hypothesis: Hypothesis
    budget_share: Decimal = Field(gt=0, le=1, description="доля общего бюджета, 0..1")
    interest_keywords: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()
    age_min: int | None = Field(default=None, ge=13, le=65)
    age_max: int | None = Field(default=None, ge=13, le=65)
    rationale: str = ""


class MediaPlanProposal(AgentModel):
    objective: Objective
    objective_rationale: str = ""
    groups: tuple[GroupProposal, ...] = Field(min_length=MIN_GROUPS, max_length=MAX_GROUPS)
    notes: str = ""


# --- Тексты ----------------------------------------------------------------


class AdVariant(AgentModel):
    kind: Literal["existing_post", "new_creative"]
    creative_id: str | None = None
    primary_text: str = ""
    headline: str = ""
    cta: str = "LEARN_MORE"

    @model_validator(mode="after")
    def _check_kind(self) -> "AdVariant":
        if self.kind == "existing_post" and not self.creative_id:
            raise ValueError("для existing_post нужен creative_id")
        if self.kind == "new_creative" and not (self.primary_text and self.headline):
            raise ValueError("для new_creative нужны текст и заголовок")
        return self


class AdCopySet(AgentModel):
    variants: tuple[AdVariant, ...] = Field(min_length=MIN_VARIANTS, max_length=MAX_VARIANTS)


# --- Контролёр -------------------------------------------------------------


class ChecklistItem(AgentModel):
    rule: int = Field(ge=1, le=9, description="номер пункта чек-листа Контролёра")
    ok: bool | None = Field(default=None, description="None — пункт не проверялся")
    note: str = ""


class Violation(AgentModel):
    rule: int = Field(ge=1, le=9)
    description: str
    fix: str


class AdVerdict(AgentModel):
    """Вердикт по одному объявлению.

    `needs_review` (не все пункты проверены) вне демо-уровня НЕ пропускает объявление в Meta
    автоматически: решает сервис (показывает пользователю или требует ключ Claude).
    """

    ad_index: int = Field(ge=0)
    checks: tuple[ChecklistItem, ...] = ()
    violations: tuple[Violation, ...] = ()

    @property
    def status(self) -> Literal["ok", "violation", "needs_review"]:
        """`needs_review` — не все 9 пунктов проверены: решает детерминированная проверка policy."""
        if self.violations or any(c.ok is False for c in self.checks):
            return "violation"
        passed = {c.rule for c in self.checks if c.ok is True}
        return "ok" if passed == set(range(1, 10)) else "needs_review"

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class ComplianceVerdict(AgentModel):
    ads: tuple[AdVerdict, ...]
    category: SpecialCategory | None = None
    category_rationale: str = ""
    adult_only: bool = False


# --- Оптимизатор, отчёты, разбор текста ------------------------------------


class Explanation(AgentModel):
    decision_index: int = Field(ge=0)
    text: str


class OptimizerSuggestion(AgentModel):
    action: Literal[
        "pause_ad", "pause_group", "increase_budget", "decrease_budget", "new_creative", "note"
    ]
    target: str = Field(description="название группы/объявления из входных данных")
    reason: str


class OptimizerOutput(AgentModel):
    explanations: tuple[Explanation, ...] = ()
    suggestions: tuple[OptimizerSuggestion, ...] = ()


class ReportSection(AgentModel):
    title: str
    text: str


class Report(AgentModel):
    period: Literal["day", "week"]
    summary: str
    sections: tuple[ReportSection, ...] = ()
    insights: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()


class IntentKind(StrEnum):
    ADD_COMPANY = "add_company"
    SET_LIMIT = "set_limit"
    PAUSE = "pause"
    RESUME = "resume"
    HALT = "halt"
    REPORT = "report"
    EDIT_PLAN = "edit_plan"
    ADD_FACT = "add_fact"
    SETTING = "setting"
    UNKNOWN = "unknown"


class Intent(AgentModel):
    kind: IntentKind = IntentKind.UNKNOWN
    company: str | None = Field(default=None, description="название или ник компании")
    instagram_handle: str | None = None
    website: str | None = None
    geo: str | None = None
    objective: Objective | None = None
    amount_text: str | None = Field(
        default=None, max_length=60,
        description="фрагмент с суммой дословно, как написал пользователь; число и валюту разбирает код",
    )
    report_period: Literal["day", "week"] | None = None
    text: str | None = Field(default=None, description="факт, правка плана или значение настройки")
    comment: str = ""
