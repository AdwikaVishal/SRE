"""
optimized_engine_adapter.py — Extended engine adapter with Part 2 optimizations.

Wraps the benchmark engine adapter to integrate all Part 2 modules:
- DecoySuppressionEngine: aggressive decoy detection
- TwoStageRetrieval: precision + recall balancing
- RemediationOptimizer: historical success-based ranking
- FamilyRepresentation: family diversity in top-5

All optimizations are pluggable via constructor flags.
Fully backward compatible with existing bench harness.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Optional

# Setup paths to ensure imports work
ROOT = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.join(ROOT, "Anvil-P-E", "bench-p02-context")
sys.path.insert(0, ROOT)
sys.path.insert(0, BENCH)

from Anvil_P_E.bench_p02_context.adapters.engine import Engine

from decoy_suppression import DecoySuppressionEngine
from family_representation import FamilyRepresentation
from remediation_optimizer import RemediationOptimizer
from two_stage_retrieval import TwoStageRetrieval


class OptimizedEngineAdapter(Engine):
    """
    Extended engine adapter with all Part 2 optimizations.

    Extends the benchmark Engine to wrap reconstruct_context() with:
    1. Decoy suppression on related_events
    2. Two-stage retrieval on similar_past_incidents
    3. Remediation optimization on suggested_remediations
    4. Family deduplication on final top-5

    All optimizations are optional via constructor flags.
    Maintains full backward compatibility with harness.
    """

    def __init__(
        self,
        use_two_stage: bool = True,
        use_remediation_optimizer: bool = True,
        use_family_rep: bool = True,
        use_decoy_suppression: bool = True,
        config: Optional[dict] = None,
    ):
        """
        Initialize optimized adapter with optional optimizations.

        Args:
            use_two_stage: Enable two-stage retrieval (Stage A precision + Stage B recall)
            use_remediation_optimizer: Enable Bayesian remediation ranking
            use_family_rep: Enable family diversity in top-5
            use_decoy_suppression: Enable decoy detection and confidence suppression
            config: Optional config dict with weights/thresholds
                {
                    "same_cid_boost": float,
                    "cross_cid_penalty": float,
                    ... (other engine params)
                    "stageA_min_similarity": float,
                    "stageB_min_similarity": float,
                    "decoy_confidence_multiplier": float,
                    "decoy_similarity_cap": float,
                }
        """
        super().__init__()

        self.use_two_stage = use_two_stage
        self.use_remediation_optimizer = use_remediation_optimizer
        self.use_family_rep = use_family_rep
        self.use_decoy_suppression = use_decoy_suppression

        # Merge config with defaults
        if config:
            self._cfg.update(config)

        # Initialize optimizers
        if self.use_decoy_suppression:
            self.decoy_engine = DecoySuppressionEngine(
                decoy_confidence_multiplier=self._cfg.get(
                    "decoy_confidence_multiplier", 0.60
                ),
                decoy_similarity_cap=self._cfg.get("decoy_similarity_cap", 0.45),
            )
        else:
            self.decoy_engine = None

        if self.use_two_stage:
            self.two_stage = TwoStageRetrieval(
                stageA_min_similarity=self._cfg.get("stageA_min_similarity", 0.60),
                stageB_min_similarity=self._cfg.get("stageB_min_similarity", 0.50),
                min_stageA_results=self._cfg.get("min_stageA_results", 3),
                max_results=self._cfg.get("max_results", 5),
            )
        else:
            self.two_stage = None

        if self.use_remediation_optimizer:
            self.remediation_optimizer = RemediationOptimizer(
                prior_success_rate=self._cfg.get("prior_success_rate", 0.5),
                min_history_count=self._cfg.get("min_history_count", 2),
            )
        else:
            self.remediation_optimizer = None

        # FamilyRepresentation is a set of static methods, no init needed

    def reconstruct_context(
        self,
        signal: dict,
        mode: str = "fast",
    ) -> dict[str, Any]:
        """
        Reconstruct incident context with optimizations applied.

        Overrides parent to:
        1. Call parent assembler (all original logic)
        2. Apply decoy suppression to related_events
        3. Apply two-stage retrieval to similar_past_incidents
        4. Apply remediation optimization to suggested_remediations
        5. Apply family deduplication to final top-5

        Returns same context schema as parent, with optimized rankings.

        Args:
            signal: IncidentSignal dict
            mode: "fast" or "deep" evaluation mode

        Returns:
            Context dict with optimized rankings
        """
        # Step 0: Call parent to get baseline context
        context = super().reconstruct_context(signal, mode=mode)

        # If parent returned empty context, return as-is
        if not context or context.get("incident_id") is None:
            return context

        # Extract components from context
        related_events = context.get("related_events", [])
        similar_past = context.get("similar_past_incidents", [])
        remediations = context.get("suggested_remediations", [])

        # ================================================================
        # Step 1: Decoy suppression
        # ================================================================
        decoy_analysis = None
        if self.use_decoy_suppression and self.decoy_engine:
            is_decoy = self.decoy_engine.is_likely_decoy(signal, related_events)
            policy = self.decoy_engine.build_suppression_policy(related_events)

            # Apply suppression to matches and remediations
            similar_past = self.decoy_engine.apply_suppression(similar_past, policy)
            remediations = self.decoy_engine.apply_suppression(remediations, policy)

            # Record analysis for diagnostics
            decoy_analysis = self.decoy_engine.analyze_decoy_risk(
                signal, related_events, similar_past
            )

        # ================================================================
        # Step 2: Two-stage retrieval
        # ================================================================
        if self.use_two_stage and self.two_stage:
            evidence_dict = self.two_stage.build_evidence_dict(related_events)
            similar_past = self.two_stage.select_top_k(
                similar_past, mode=mode, evidence_dict=evidence_dict
            )

        # ================================================================
        # Step 3: Remediation optimization
        # ================================================================
        if self.use_remediation_optimizer and self.remediation_optimizer:
            # Rank remediations by historical success
            optimized_rems = self.remediation_optimizer.rank_remediations(similar_past)

            # If we got ranked remediations, use them; otherwise keep originals
            if optimized_rems:
                remediations = optimized_rems
            else:
                # Fallback: keep original remediations
                pass

        # ================================================================
        # Step 4: Family deduplication (final top-5)
        # ================================================================
        if self.use_family_rep:
            similar_past = FamilyRepresentation.deduplicate_families(
                similar_past, keep_top_k_per_family=1
            )

            # Also ensure we have exactly 5 (or fewer if not enough matches)
            similar_past = similar_past[:5]

        # ================================================================
        # Update context with optimized results
        # ================================================================
        context["related_events"] = related_events
        context["similar_past_incidents"] = similar_past
        context["suggested_remediations"] = remediations

        # Attach diagnostic info if decoy suppression was applied
        if decoy_analysis:
            context["_optimization_diagnostics"] = {
                "decoy_analysis": decoy_analysis,
                "optimizations_applied": {
                    "decoy_suppression": self.use_decoy_suppression,
                    "two_stage_retrieval": self.use_two_stage,
                    "remediation_optimizer": self.use_remediation_optimizer,
                    "family_representation": self.use_family_rep,
                },
            }

        return context

    def get_config(self) -> dict[str, Any]:
        """Return current engine configuration."""
        return {
            "engine_config": self._cfg,
            "optimizations": {
                "use_two_stage": self.use_two_stage,
                "use_remediation_optimizer": self.use_remediation_optimizer,
                "use_family_rep": self.use_family_rep,
                "use_decoy_suppression": self.use_decoy_suppression,
            },
        }

    def set_config(self, config: dict[str, Any]) -> None:
        """Update engine configuration."""
        if "engine_config" in config:
            self._cfg.update(config["engine_config"])
        if "decoy_similarity_cap" in config:
            self._cfg["decoy_similarity_cap"] = config["decoy_similarity_cap"]
        if "decoy_confidence_multiplier" in config:
            self._cfg["decoy_confidence_multiplier"] = config[
                "decoy_confidence_multiplier"
            ]


# ============================================================================
# Factory function for integration with bench_run.py
# ============================================================================


def optimized_adapter_factory(config: Optional[dict] = None):
    """
    Factory function to create OptimizedEngineAdapter for use with harness.

    Args:
        config: Optional config dict to pass to adapter

    Returns:
        OptimizedEngineAdapter instance
    """
    return OptimizedEngineAdapter(
        use_two_stage=True,
        use_remediation_optimizer=True,
        use_family_rep=True,
        use_decoy_suppression=True,
        config=config,
    )


# ============================================================================
# Example usage
# ============================================================================

if __name__ == "__main__":
    # Example: create adapter with custom config
    custom_config = {
        "stageA_min_similarity": 0.55,
        "decoy_similarity_cap": 0.40,
    }

    adapter = OptimizedEngineAdapter(config=custom_config)
    print(f"Adapter created with optimizations: {adapter.get_config()}")
