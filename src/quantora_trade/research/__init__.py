"""Leakage-safe offline research primitives with no execution capability."""

from quantora_trade.research.baseline import (
    AdvisoryPrediction,
    BaselineTrainingConfig,
    ClassificationMetrics,
    LogisticBaselineModel,
    evaluate_classifier,
    train_logistic_baseline,
)
from quantora_trade.research.dataset import (
    DatasetBuildConfig,
    DirectionLabel,
    LabeledExample,
    ResearchDataset,
    build_research_dataset,
)
from quantora_trade.research.features import (
    FEATURE_NAMES,
    FeaturePipelineConfig,
    FeatureSet,
    FeatureVector,
    build_feature_set,
)
from quantora_trade.research.registry import (
    AdvisoryInferencePort,
    ModelCard,
    ModelLifecycle,
    ModelRegistry,
    RegistryInferenceService,
)
from quantora_trade.research.walk_forward import (
    FoldEvaluation,
    NoSkillMetrics,
    WalkForwardConfig,
    WalkForwardFold,
    WalkForwardResult,
    build_walk_forward_folds,
    run_walk_forward,
)

__all__ = [
    "FEATURE_NAMES",
    "AdvisoryInferencePort",
    "AdvisoryPrediction",
    "BaselineTrainingConfig",
    "ClassificationMetrics",
    "DatasetBuildConfig",
    "DirectionLabel",
    "FeaturePipelineConfig",
    "FeatureSet",
    "FeatureVector",
    "FoldEvaluation",
    "LabeledExample",
    "LogisticBaselineModel",
    "ModelCard",
    "ModelLifecycle",
    "ModelRegistry",
    "NoSkillMetrics",
    "RegistryInferenceService",
    "ResearchDataset",
    "WalkForwardConfig",
    "WalkForwardFold",
    "WalkForwardResult",
    "build_feature_set",
    "build_research_dataset",
    "build_walk_forward_folds",
    "evaluate_classifier",
    "run_walk_forward",
    "train_logistic_baseline",
]
