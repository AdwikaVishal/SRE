"""
config_manager.py — Configuration management for optimization experiments.

Handles loading, saving, and exporting engine configurations.
Tracks experiment metadata for reproducibility.
Provides configuration summaries for documentation.

Usage:
    manager = ConfigManager()
    config = manager.load_baseline_config()
    manager.save_config(config, "my_config.json")
    summary = manager.export_config_summary(config)
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional


class ConfigManager:
    """Manages engine configurations for optimization experiments."""

    # Default baseline configuration (from engine.py)
    DEFAULT_CONFIG = {
        "same_cid_boost": 0.32,
        "cross_cid_penalty": 0.22,
        "action_success_weight": 0.12,
        "topology_neighbor_boost": 0.10,
        "graph_distance_penalty": 0.10,
        "evidence_boost": 0.08,
        "decoy_cap_similarity": 0.39,
        "decoy_cap_remediation": 0.39,
        "stageA_min_similarity": 0.52,
        "stageB_min_similarity": 0.50,
        "min_stageA_results": 3,
        "max_results": 5,
        "decoy_confidence_multiplier": 0.60,
        "decoy_similarity_cap": 0.45,
        "prior_success_rate": 0.5,
        "min_history_count": 2,
    }

    def __init__(self):
        """Initialize config manager."""
        self.metadata: dict[str, Any] = {}

    def load_baseline_config(self) -> dict[str, Any]:
        """
        Load baseline configuration (engine defaults).

        Returns:
            Default engine configuration dict
        """
        return dict(self.DEFAULT_CONFIG)

    def save_config(
        self,
        config: dict[str, Any],
        filename: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """
        Save configuration to JSON file with metadata.

        Args:
            config: Configuration dict
            filename: Output filename
            metadata: Optional metadata dict with timing, source, etc.
        """
        if metadata is None:
            metadata = {}

        # Add timestamp if not present
        if "timestamp" not in metadata:
            metadata["timestamp"] = datetime.now().isoformat()

        output = {
            "config": config,
            "metadata": metadata,
        }

        with open(filename, "w") as f:
            json.dump(output, f, indent=2)

    def load_config(self, filename: str) -> dict[str, Any]:
        """
        Load configuration from JSON file.

        Args:
            filename: Input filename

        Returns:
            Configuration dict
        """
        with open(filename) as f:
            data = json.load(f)

        if isinstance(data, dict) and "config" in data:
            self.metadata = data.get("metadata", {})
            return data["config"]
        else:
            # Plain config file (no metadata)
            return data

    def export_config_summary(
        self,
        config: dict[str, Any],
        include_defaults: bool = True,
    ) -> str:
        """
        Export human-readable configuration summary.

        Args:
            config: Configuration dict
            include_defaults: Show which values differ from baseline

        Returns:
            Markdown-formatted summary
        """
        baseline = self.DEFAULT_CONFIG if include_defaults else {}

        lines = [
            "# Configuration Summary\n",
            "## Engine Parameters\n\n",
        ]

        # Group parameters by category
        categories = {
            "Similarity/Evidence": [
                "same_cid_boost",
                "cross_cid_penalty",
                "evidence_boost",
                "action_success_weight",
            ],
            "Topology": [
                "topology_neighbor_boost",
                "graph_distance_penalty",
            ],
            "Decoy Suppression": [
                "decoy_cap_similarity",
                "decoy_cap_remediation",
                "decoy_confidence_multiplier",
                "decoy_similarity_cap",
            ],
            "Two-Stage Retrieval": [
                "stageA_min_similarity",
                "stageB_min_similarity",
                "min_stageA_results",
                "max_results",
            ],
            "Remediation Optimizer": [
                "prior_success_rate",
                "min_history_count",
            ],
        }

        for category, params in categories.items():
            lines.append(f"### {category}\n\n")

            for param in params:
                value = config.get(param)
                if value is None:
                    continue

                baseline_val = baseline.get(param)

                if baseline_val is not None and baseline_val != value:
                    # Changed from baseline
                    marker = "⚠️ "
                    status = f"(default: {baseline_val})"
                else:
                    marker = "✓ "
                    status = "(default)"

                lines.append(f"- {marker}**{param}**: {value} {status}\n")

            lines.append("\n")

        # Summary of changes
        if include_defaults:
            changed = [k for k in config.keys() if config[k] != baseline.get(k)]
            if changed:
                lines.append(f"## Changes from Baseline\n\n")
                lines.append(f"**Modified parameters:** {len(changed)}\n\n")
                for param in sorted(changed):
                    baseline_val = baseline.get(param, "N/A")
                    new_val = config.get(param, "N/A")
                    lines.append(f"- {param}: {baseline_val} → {new_val}\n")

        return "".join(lines)

    def merge_configs(
        self,
        base: dict[str, Any],
        overrides: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Merge override config into base config.

        Args:
            base: Base configuration
            overrides: Override values

        Returns:
            Merged configuration
        """
        result = dict(base)
        result.update(overrides)
        return result

    def list_difference(
        self,
        config1: dict[str, Any],
        config2: dict[str, Any],
    ) -> dict[str, tuple[Any, Any]]:
        """
        List differences between two configs.

        Args:
            config1: First config
            config2: Second config

        Returns:
            {param: (value1, value2), ...} for differing parameters
        """
        differences = {}

        for key in set(list(config1.keys()) + list(config2.keys())):
            val1 = config1.get(key)
            val2 = config2.get(key)

            if val1 != val2:
                differences[key] = (val1, val2)

        return differences

    def validate_config(self, config: dict[str, Any]) -> tuple[bool, list[str]]:
        """
        Validate configuration for reasonable values.

        Args:
            config: Configuration to validate

        Returns:
            (is_valid, list_of_errors)
        """
        errors = []

        # Validate similarity thresholds
        stage_a = config.get("stageA_min_similarity", 0.5)
        stage_b = config.get("stageB_min_similarity", 0.5)

        if not (0.0 <= stage_a <= 1.0):
            errors.append(f"stageA_min_similarity out of range: {stage_a}")
        if not (0.0 <= stage_b <= 1.0):
            errors.append(f"stageB_min_similarity out of range: {stage_b}")
        if stage_a < stage_b:
            errors.append("stageA_min_similarity should be >= stageB_min_similarity")

        # Validate decoy parameters
        decoy_cap = config.get("decoy_similarity_cap", 0.45)
        decoy_mult = config.get("decoy_confidence_multiplier", 0.60)

        if not (0.0 <= decoy_cap <= 1.0):
            errors.append(f"decoy_similarity_cap out of range: {decoy_cap}")
        if not (0.0 <= decoy_mult <= 1.0):
            errors.append(f"decoy_confidence_multiplier out of range: {decoy_mult}")

        # Validate remediation
        prior = config.get("prior_success_rate", 0.5)
        if not (0.0 <= prior <= 1.0):
            errors.append(f"prior_success_rate out of range: {prior}")

        # Validate counts
        min_hist = config.get("min_history_count", 2)
        max_res = config.get("max_results", 5)
        if min_hist < 1:
            errors.append(f"min_history_count should be >= 1: {min_hist}")
        if max_res < 1:
            errors.append(f"max_results should be >= 1: {max_res}")

        return len(errors) == 0, errors


# ============================================================================
# Example usage
# ============================================================================

if __name__ == "__main__":
    manager = ConfigManager()

    # Load and save baseline
    baseline = manager.load_baseline_config()
    print("Baseline config keys:", len(baseline))

    # Create a variant
    variant = manager.merge_configs(
        baseline,
        {
            "stageA_min_similarity": 0.65,
            "decoy_similarity_cap": 0.40,
        },
    )

    # Save variant
    manager.save_config(
        variant,
        "variant_config.json",
        metadata={
            "name": "variant_1",
            "description": "Stronger Stage A, stricter decoy cap",
        },
    )

    # Generate summary
    summary = manager.export_config_summary(variant)
    print(summary)

    # Validate
    is_valid, errors = manager.validate_config(variant)
    print(f"\nValid: {is_valid}")
    if errors:
        print("Errors:")
        for err in errors:
            print(f"  - {err}")

    # Compare
    diffs = manager.list_difference(baseline, variant)
    print(f"\nDifferences: {len(diffs)}")
    for param, (val1, val2) in diffs.items():
        print(f"  {param}: {val1} → {val2}")
