"""Действия, которые Исполнитель просит проверить у политики."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from target_ai.money import Money

from .types import GoalKind

INSTAGRAM = "instagram"
MOBILE = "mobile"
ALLOWED_POSITIONS = frozenset({"stream", "story", "reels"})


@dataclass(frozen=True)
class Targeting:
    """Платформенная часть таргетинга; Исполнитель отклоняет всё, кроме Instagram."""

    publisher_platforms: tuple[str, ...] = ()
    instagram_positions: tuple[str, ...] = ()
    device_platforms: tuple[str, ...] = ()
    placement_soft_opt_out: bool = False

    @classmethod
    def instagram_only(cls, positions: tuple[str, ...]) -> Targeting:
        return cls((INSTAGRAM,), positions, (MOBILE,))


@dataclass(frozen=True)
class NewGroup:
    budget: Money
    goal_kind: GoalKind
    targeting: Targeting | None
    id: str = ""  # идентификатор новой группы: входит в ключ подтверждения запуска


@dataclass(frozen=True)
class Launch:
    """Запуск плана: включает группы, поэтому всегда увеличивает расход."""

    groups: tuple[NewGroup, ...]
    spend_cap: Money | None = None
    tag: str = ""  # кампания/метка запуска: ключ одного запуска не подходит к другому


@dataclass(frozen=True)
class Activate:
    object_id: str
    object_kind: Literal["campaign", "group", "ad"]


@dataclass(frozen=True)
class Pause:
    object_id: str


@dataclass(frozen=True)
class SetDailyBudget:
    group_id: str
    new_budget: Money
    lowers_for_limit: bool = False  # снижение ради лимита: без ограничений 20% и 24 ч


@dataclass(frozen=True)
class SetSpendCap:
    campaign_id: str
    cap: Money


@dataclass(frozen=True)
class SetMonthlyLimit:
    new_limit: Money


@dataclass(frozen=True)
class CreateObject:
    """Создание объекта на паузе; деньги не тратит, но таргетинг и суммы проверяются."""

    kind: Literal["campaign", "group", "ad", "creative", "audience"]
    parent_id: str | None = None
    budget: Money | None = None
    goal_kind: GoalKind | None = None
    targeting: Targeting | None = None


@dataclass(frozen=True)
class Delete:
    object_id: str


Action = (
    Launch | Activate | Pause | SetDailyBudget | SetSpendCap | SetMonthlyLimit | CreateObject | Delete
)
