"""Immutable research-only model registry and advisory inference boundary."""

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from quantora_trade.research.baseline import AdvisoryPrediction, LogisticBaselineModel
from quantora_trade.research.features import FeatureVector

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ModelLifecycle(StrEnum):
    CHALLENGER = "CHALLENGER"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    RETIRED = "RETIRED"


@dataclass(frozen=True, slots=True)
class ModelCard:
    model: LogisticBaselineModel
    lifecycle: ModelLifecycle
    evaluation_sha256: str
    registered_at: datetime
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.registered_at.tzinfo is None or self.registered_at.utcoffset() != UTC.utcoffset(
            self.registered_at
        ):
            raise ValueError("model registration time must be timezone-aware UTC")
        if not _SHA256.fullmatch(self.evaluation_sha256):
            raise ValueError("model evaluation checksum must be a SHA-256")
        if not self.limitations or any(not item.strip() for item in self.limitations):
            raise ValueError("model card requires explicit limitations")
        if tuple(sorted(set(self.limitations))) != self.limitations:
            raise ValueError("model limitations must be unique and sorted")
        if self.lifecycle not in {
            ModelLifecycle.CHALLENGER,
            ModelLifecycle.RESEARCH_ONLY,
            ModelLifecycle.RETIRED,
        }:
            raise ValueError("unsupported model lifecycle")


@dataclass(frozen=True, slots=True)
class ModelRegistry:
    """Persistent-value registry; updates return a new registry snapshot."""

    cards: tuple[ModelCard, ...] = ()

    def __post_init__(self) -> None:
        ids = tuple(card.model.id for card in self.cards)
        if len(ids) != len(set(ids)):
            raise ValueError("model registry contains duplicate model IDs")
        versions = tuple(card.model.version for card in self.cards)
        if len(versions) != len(set(versions)):
            raise ValueError("model registry contains duplicate versions")

    def register(self, card: ModelCard) -> "ModelRegistry":
        if card.lifecycle is not ModelLifecycle.RESEARCH_ONLY:
            raise ValueError("new models must enter the registry as RESEARCH_ONLY")
        return ModelRegistry(cards=(*self.cards, card))

    def change_lifecycle(self, *, model_id: UUID, lifecycle: ModelLifecycle) -> "ModelRegistry":
        if lifecycle not in {ModelLifecycle.CHALLENGER, ModelLifecycle.RETIRED}:
            raise ValueError("registry permits promotion only to CHALLENGER or RETIRED")
        if not any(card.model.id == model_id for card in self.cards):
            raise KeyError(model_id)
        current = self.get(model_id)
        if current.lifecycle is ModelLifecycle.RETIRED:
            raise ValueError("retired model lifecycle cannot be reactivated")
        if lifecycle is ModelLifecycle.CHALLENGER and any(
            card.lifecycle is ModelLifecycle.CHALLENGER and card.model.id != model_id
            for card in self.cards
        ):
            raise ValueError("registry permits only one active challenger")
        return ModelRegistry(
            cards=tuple(
                replace(card, lifecycle=lifecycle) if card.model.id == model_id else card
                for card in self.cards
            )
        )

    def get(self, model_id: UUID) -> ModelCard:
        try:
            return next(card for card in self.cards if card.model.id == model_id)
        except StopIteration as error:
            raise KeyError(model_id) from error


class AdvisoryInferencePort(Protocol):
    """Narrow offline interface; no broker or order method can be exposed here."""

    def predict(self, *, model_id: UUID, features: FeatureVector) -> AdvisoryPrediction: ...


@dataclass(frozen=True, slots=True)
class RegistryInferenceService:
    registry: ModelRegistry

    def predict(self, *, model_id: UUID, features: FeatureVector) -> AdvisoryPrediction:
        card = self.registry.get(model_id)
        if card.lifecycle is ModelLifecycle.RETIRED:
            raise ValueError("retired model cannot perform inference")
        return card.model.predict(features)
