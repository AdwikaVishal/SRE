"""
family_representation.py — Incident family grouping and representation.

Groups incidents by family (suffix-based) to prevent same-family clustering
in top-5 results. Improves precision@5 by diversifying families.

Families are identified by incident ID suffix:
  "INC-X-5" → family "5"
  "INC-Y-5" → family "5"

Goal: Ensure top-5 contains diverse families, not 3 from family 5.
"""

from __future__ import annotations

from statistics import mean
from typing import Any, Optional


class FamilyRepresentation:
    """
    Manages incident grouping by family for diversity optimization.

    Purpose:
    - Extract family identifier from incident IDs
    - Group similar matches by family
    - Select best representative per family
    - Deduplicate to avoid same-family clustering
    - Compute family-level confidence margins
    """

    @staticmethod
    def family_id_from_incident(incident_id: str) -> str:
        """
        Extract family ID from incident ID.

        Rules:
        - If format "INC-*-N": family = "N" (suffix)
        - Otherwise: family = incident_id itself

        Args:
            incident_id: Incident identifier (e.g., "INC-X-5")

        Returns:
            Family identifier (e.g., "5")
        """
        if not incident_id:
            return "unknown"

        if incident_id.startswith("INC-") and "-" in incident_id:
            # Extract suffix after last hyphen
            return incident_id.rsplit("-", 1)[-1]

        # Fallback: use full incident ID as family
        return incident_id

    @staticmethod
    def group_by_family(matches: list[dict]) -> dict[str, list[dict]]:
        """
        Group incident matches by family.

        Args:
            matches: List of incident matches

        Returns:
            {"family_id": [matches], ...}
        """
        families: dict[str, list[dict]] = {}

        for match in matches:
            incident_id = match.get("incident_id", "")
            family = FamilyRepresentation.family_id_from_incident(incident_id)
            families.setdefault(family, []).append(match)

        return families

    @staticmethod
    def select_representative(family_matches: list[dict]) -> dict:
        """
        Select best representative from family.

        Picks highest similarity incident from family.

        Args:
            family_matches: Incidents from same family

        Returns:
            Single best match dict
        """
        if not family_matches:
            return {}

        return max(family_matches, key=lambda m: float(m.get("similarity", 0.0)))

    @staticmethod
    def deduplicate_families(
        matches: list[dict],
        keep_top_k_per_family: int = 1,
    ) -> list[dict]:
        """
        Remove same-family clustering by keeping only top-k per family.

        Prevents scenarios like:
        - Top-5: [INC-X-5 (0.90), INC-Y-5 (0.85), INC-Z-5 (0.80), INC-A-3, INC-B-2]

        Instead:
        - Top-5: [INC-X-5 (0.90), INC-A-3, INC-B-2, INC-C-7, INC-D-1]

        Args:
            matches: List of matches (pre-ranked by similarity)
            keep_top_k_per_family: Max matches per family (default 1)

        Returns:
            Deduplicated list with diversity
        """
        families = FamilyRepresentation.group_by_family(matches)

        deduplicated = []
        # Iterate families in order of best similarity
        for family_id in sorted(
            families.keys(),
            key=lambda f: max(float(m.get("similarity", 0.0)) for m in families[f]),
            reverse=True,
        ):
            bucket = families[family_id]
            # Sort within family by similarity
            bucket.sort(key=lambda m: float(m.get("similarity", 0.0)), reverse=True)
            # Keep top-k from this family
            deduplicated.extend(bucket[:keep_top_k_per_family])

        return deduplicated

    @staticmethod
    def compute_family_posterior(family_matches: list[dict]) -> dict[str, Any]:
        """
        Compute aggregate statistics for a family of matches.

        Args:
            family_matches: Incidents with same family suffix

        Returns:
            {
                "family_id": str,
                "mean_similarity": float,
                "best_similarity": float,
                "second_best_similarity": float,
                "count": int,
                "confidence_margin": float,  # Gap to 2nd-best
                "variance": float,  # Std dev of similarities
            }
        """
        if not family_matches:
            return {
                "family_id": "unknown",
                "mean_similarity": 0.0,
                "best_similarity": 0.0,
                "second_best_similarity": 0.0,
                "count": 0,
                "confidence_margin": 0.0,
                "variance": 0.0,
            }

        # Extract similarities
        sims = sorted(
            [float(m.get("similarity", 0.0)) for m in family_matches],
            reverse=True,
        )

        family_id = FamilyRepresentation.family_id_from_incident(
            family_matches[0].get("incident_id", "")
        )

        best = sims[0] if sims else 0.0
        second_best = sims[1] if len(sims) > 1 else 0.0
        mean_sim = mean(sims) if sims else 0.0

        # Compute variance (squared deviations)
        variance = mean((s - mean_sim) ** 2 for s in sims) if sims else 0.0

        confidence_margin = best - second_best

        return {
            "family_id": family_id,
            "mean_similarity": round(mean_sim, 3),
            "best_similarity": round(best, 3),
            "second_best_similarity": round(second_best, 3),
            "count": len(family_matches),
            "confidence_margin": round(confidence_margin, 3),
            "variance": round(variance, 4),
        }

    @staticmethod
    def rank_families_by_strength(matches: list[dict]) -> list[dict]:
        """
        Rank families by their posterior confidence.

        Families with high mean similarity and large confidence margins
        are ranked higher.

        Args:
            matches: List of incident matches

        Returns:
            [
                {
                    "family_id": str,
                    "posterior": dict,  # from compute_family_posterior
                    "rank_score": float,
                }
            ]
        """
        families = FamilyRepresentation.group_by_family(matches)

        ranked_families = []
        for family_id, family_matches in families.items():
            posterior = FamilyRepresentation.compute_family_posterior(family_matches)

            # Rank score: weighted combination
            # Mean similarity (40%) + confidence margin (40%) + count credibility (20%)
            count_credibility = min(1.0, posterior["count"] / 5.0)
            rank_score = (
                0.40 * posterior["mean_similarity"]
                + 0.40 * posterior["confidence_margin"]
                + 0.20 * count_credibility
            )

            ranked_families.append(
                {
                    "family_id": family_id,
                    "posterior": posterior,
                    "rank_score": round(rank_score, 3),
                }
            )

        # Sort by rank_score descending
        ranked_families.sort(key=lambda x: x["rank_score"], reverse=True)

        return ranked_families

    @staticmethod
    def diversify_by_family(
        matches: list[dict],
        target_count: int = 5,
        keep_per_family: int = 1,
    ) -> list[dict]:
        """
        Diversify matches to target_count, respecting family limits.

        Algorithm:
        1. Group by family
        2. Rank families by strength
        3. Take top-k per family until target_count reached

        Args:
            matches: Pre-ranked incident matches
            target_count: Target number of results (e.g., 5)
            keep_per_family: Max matches per family (default 1)

        Returns:
            Diversified matches (≤target_count)
        """
        if not matches:
            return []

        families = FamilyRepresentation.group_by_family(matches)

        # Rank families
        family_ranking = FamilyRepresentation.rank_families_by_strength(matches)

        diversified = []
        for fam_info in family_ranking:
            family_id = fam_info["family_id"]
            family_matches = families[family_id]

            # Sort by similarity within family
            family_matches.sort(
                key=lambda m: float(m.get("similarity", 0.0)), reverse=True
            )

            # Take top-k from family
            diversified.extend(family_matches[:keep_per_family])

            if len(diversified) >= target_count:
                break

        # Return capped at target_count
        return diversified[:target_count]

    @staticmethod
    def is_same_family(incident_id_a: str, incident_id_b: str) -> bool:
        """
        Check if two incidents belong to same family.

        Args:
            incident_id_a: First incident ID
            incident_id_b: Second incident ID

        Returns:
            True if same family, False otherwise
        """
        fam_a = FamilyRepresentation.family_id_from_incident(incident_id_a)
        fam_b = FamilyRepresentation.family_id_from_incident(incident_id_b)
        return fam_a == fam_b

    @staticmethod
    def compute_family_diversity_score(matches: list[dict]) -> float:
        """
        Compute diversity score: fraction of unique families in top-k.

        0.0 = all same family
        1.0 = all different families

        Args:
            matches: List of incident matches

        Returns:
            Diversity score in [0.0, 1.0]
        """
        if not matches:
            return 0.0

        families = set(
            FamilyRepresentation.family_id_from_incident(m.get("incident_id", ""))
            for m in matches
        )

        return len(families) / len(matches)


# ============================================================================
# Integration helpers
# ============================================================================


def deduplicate_and_diversify(
    candidates: list[dict],
    max_results: int = 5,
) -> list[dict]:
    """
    Standalone function: deduplicate families and diversify results.

    Args:
        candidates: Pre-ranked incident matches
        max_results: Target result count

    Returns:
        Diversified, deduplicated matches
    """
    return FamilyRepresentation.diversify_by_family(
        candidates, target_count=max_results, keep_per_family=1
    )


# ============================================================================
# Example Usage
# ============================================================================

if __name__ == "__main__":
    # Example: candidates with multiple families
    candidates = [
        {
            "incident_id": "INC-X-5",
            "similarity": 0.90,
            "remediation_action": "restart",
        },
        {
            "incident_id": "INC-Y-5",
            "similarity": 0.85,
            "remediation_action": "restart",
        },
        {
            "incident_id": "INC-Z-5",
            "similarity": 0.80,
            "remediation_action": "restart",
        },
        {
            "incident_id": "INC-A-3",
            "similarity": 0.75,
            "remediation_action": "scale",
        },
        {
            "incident_id": "INC-B-2",
            "similarity": 0.70,
            "remediation_action": "drain",
        },
        {
            "incident_id": "INC-C-7",
            "similarity": 0.65,
            "remediation_action": "retry",
        },
    ]

    print("Original top-5 (maybe family-clustered):")
    for i, m in enumerate(candidates[:5]):
        fam = FamilyRepresentation.family_id_from_incident(m["incident_id"])
        print(f"  {i + 1}. {m['incident_id']} (fam={fam}, sim={m['similarity']})")

    diversified = FamilyRepresentation.diversify_by_family(candidates, target_count=5)
    print(f"\nDiversified top-5 (1 per family where possible):")
    for i, m in enumerate(diversified):
        fam = FamilyRepresentation.family_id_from_incident(m["incident_id"])
        print(f"  {i + 1}. {m['incident_id']} (fam={fam}, sim={m['similarity']})")

    diversity_score = FamilyRepresentation.compute_family_diversity_score(diversified)
    print(f"\nDiversity score (unique families / total): {diversity_score:.2f}")

    # Family ranking
    ranked = FamilyRepresentation.rank_families_by_strength(candidates)
    print(f"\nFamily ranking (by posterior strength):")
    for fam in ranked:
        print(
            f"  Family {fam['family_id']}: "
            f"mean={fam['posterior']['mean_similarity']}, "
            f"margin={fam['posterior']['confidence_margin']}, "
            f"rank={fam['rank_score']}"
        )
