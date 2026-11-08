"""Результаты операций Исполнителя (неизменяемые)."""

from __future__ import annotations

from dataclasses import dataclass

from target_ai.policy import NeedsApproval

BUTTON_RETRY = "Повторить"
BUTTON_CONTINUE = "Довести"
BUTTON_ROLLBACK = "Откатить"

# статусы LaunchResult.status
LAUNCHED = "launched"
AWAITING_DECISION = "awaiting_decision"
ALREADY_DONE = "already_done"
NEEDS_APPROVAL = "needs_approval"
VALIDATED = "validated"
ROLLED_BACK = "rolled_back"
BUSY = "busy"


@dataclass(frozen=True)
class LaunchResult:
    status: str
    message_ru: str
    plan_id: int = 0
    launch_id: int | None = None
    campaign_id: str | None = None
    failed_step: str | None = None
    created: tuple[str, ...] = ()  # id созданных (или найденных по тегу) объектов Meta
    needs_approval: NeedsApproval | None = None

    @property
    def already_done(self) -> bool:
        return self.status == ALREADY_DONE


@dataclass(frozen=True)
class RecoveryItem:
    """Прерванный запуск, найденный при старте: что успело создаться и что делать."""

    launch_id: int
    company_id: int
    step_number: int
    steps_total: int
    created: tuple[str, ...]
    message_ru: str


@dataclass(frozen=True)
class PauseReport:
    """Итог стоп-крана / паузы компании."""

    paused: tuple[str, ...]
    failed: tuple[tuple[str, str], ...]  # (id кампании, причина)
    message_ru: str


@dataclass(frozen=True)
class AppliedDecision:
    target_id: str
    kind: str
    status: str  # applied | denied | needs_approval | failed | skipped
    message_ru: str
    needs_approval: NeedsApproval | None = None


@dataclass(frozen=True)
class DecisionsReport:
    applied: tuple[AppliedDecision, ...]
    stopped: bool  # пакет остановлен: пауза или снижение не прошли
    message_ru: str


@dataclass(frozen=True)
class ChangeResult:
    """Итог одиночного изменения (возобновление, лимит, исправление, смена месяца)."""

    status: str  # done | needs_approval | partial | failed
    message_ru: str
    needs_approval: NeedsApproval | None = None
    details: tuple[str, ...] = ()

READY = "ready"
VALIDATION_FAILED = "validation_failed"
