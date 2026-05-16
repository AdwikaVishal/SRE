from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict
from itertools import product
from typing import Any

from generator import GenConfig, generate, stretch_config
from metrics import aggregate, score_match, score_remediation
from adapters.engine import Engine


def run_diag(seeds: list[int], mode: str = "fast") -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    summaries = []

    for seed in seeds:
        cfg = stretch_config(seed=seed)
        ds = generate(cfg)
        eng = Engine()
        eng.ingest(ds.train_events)
        eng.ingest(ds.eval_events)

        incident_scores = []
        for sig, gt in zip(ds.eval_signals, ds.ground_truth):
            ctx = eng.reconstruct_context({
                "incident_id": sig["incident_id"],
                "ts": sig["ts"],
                "trigger": sig.get("trigger", ""),
                "service": sig.get("service", ""),
            }, mode=mode)
            top5 = (ctx.get("similar_past_incidents") or [])[:5]
            in_topk, prec = score_match(ctx, gt, k=5)
            rem_ok = score_remediation(ctx, gt)
            fam_preds = []
            sims = []
            for m in top5:
                iid = m.get("incident_id", "")
                fam = iid.rsplit("-", 1)[-1] if iid.startswith("INC-") and "-" in iid else "DEC"
                fam_preds.append(fam)
                sims.append(float(m.get("similarity", 0.0)))

            related = ctx.get("related_events", [])
            kinds = {e.get("kind") for e in related}
            has_deploy = "deploy" in kinds
            has_trace = "trace" in kinds
            has_metric = "metric" in kinds
            has_log = "log" in kinds
            row = {
                "seed": seed,
                "incident_id": sig["incident_id"],
                "gt_family": gt.get("family"),
                "is_decoy": gt.get("family") is None,
                "signal_service": sig.get("service", ""),
                "canonical_service_id": eng.resolver.resolve(sig.get("service", "")),
                "top5_pred_families": fam_preds,
                "top5_similarities": sims,
                "top_remediation": (ctx.get("suggested_remediations") or [{}])[0].get("action"),
                "expected_remediation": gt.get("expected_remediation"),
                "confidence": float(ctx.get("confidence", 0.0)),
                "deploy_evidence": has_deploy,
                "trace_evidence": has_trace,
                "metric_evidence": has_metric,
                "log_evidence": has_log,
                "top_candidate_why": top5[0].get("rationale", "") if top5 else "none",
                "recall_hit": in_topk,
                "precision_at_5": prec,
                "remediation_ok": rem_ok,
                "failure_type": _failure_type(gt, in_topk, rem_ok, top5),
            }
            rows.append(row)
            incident_scores.append({
                "incident_id": sig["incident_id"],
                "correct_family_in_top_k": in_topk,
                "precision_at_k": prec,
                "remediation_matches": rem_ok,
                "latency_ms": 0.0,
            })

        summary = aggregate([
            type("S", (), s) for s in incident_scores
        ])
        summaries.append({"seed": seed, "summary": summary})
        eng.close()

    confusion = _confusion(rows)
    failure_counts = {}
    for r in rows:
        failure_counts[r["failure_type"]] = failure_counts.get(r["failure_type"], 0) + 1

    return {
        "summaries": summaries,
        "rows": rows,
        "failure_counts": failure_counts,
        "confusion": confusion,
    }


def _failure_type(gt: dict[str, Any], recall_hit: bool, rem_ok: bool, top5: list[dict]) -> str:
    if gt.get("family") is None:
        confident = [m for m in top5 if float(m.get("similarity", 0.0)) >= 0.5]
        return "decoy_fp" if confident else "ok"
    if not recall_hit:
        return "recall_miss"
    if not rem_ok:
        return "remediation_mismatch"
    fams = [m.get("incident_id", "").rsplit("-", 1)[-1] for m in top5 if m.get("incident_id", "").startswith("INC-")]
    if len([f for f in fams if str(f) == str(gt.get("family"))]) <= 1:
        return "precision_contamination"
    return "ok"


def _confusion(rows: list[dict]) -> dict[str, dict[str, int]]:
    c: dict[str, dict[str, int]] = {}
    for r in rows:
        gt = str(r["gt_family"])
        top = str(r["top5_pred_families"][0] if r["top5_pred_families"] else "NONE")
        c.setdefault(gt, {})
        c[gt][top] = c[gt].get(top, 0) + 1
    return c


def write_outputs(diag: dict[str, Any], out_prefix: str) -> None:
    jpath = out_prefix + ".json"
    cpath = out_prefix + ".csv"
    with open(jpath, "w") as f:
        json.dump({k: v for k, v in diag.items() if k != "rows"}, f, indent=2)

    fields = list(diag["rows"][0].keys()) if diag["rows"] else []
    with open(cpath, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in diag["rows"]:
            rr = dict(r)
            rr["top5_pred_families"] = "|".join(map(str, rr["top5_pred_families"]))
            rr["top5_similarities"] = "|".join(map(lambda x: f"{x:.3f}", rr["top5_similarities"]))
            w.writerow(rr)


def sweep(seeds: list[int]) -> dict[str, Any]:
    grid = {
        "same_cid_boost": [0.28, 0.32, 0.36],
        "cross_cid_penalty": [0.18, 0.22, 0.26],
        "stageA_min_similarity": [0.48, 0.52, 0.56],
        "decoy_cap_similarity": [0.35, 0.39, 0.44],
    }
    keys = list(grid.keys())
    best = {"score": -1.0, "cfg": None, "metrics": None}

    for vals in product(*[grid[k] for k in keys]):
        cfg_vals = dict(zip(keys, vals))
        agg = _eval_cfg(seeds, cfg_vals)
        score = 0.30 * agg["recall@5"] + 0.15 * agg["precision@5_mean"] + 0.20 * agg["remediation_acc"]
        if score > best["score"]:
            best = {"score": score, "cfg": cfg_vals, "metrics": agg}
    return best


def _eval_cfg(seeds: list[int], cfg_vals: dict[str, float]) -> dict[str, float]:
    per = []
    for seed in seeds:
        cfg = stretch_config(seed=seed)
        ds = generate(cfg)
        eng = Engine()
        eng._cfg.update(cfg_vals)
        eng.ingest(ds.train_events)
        eng.ingest(ds.eval_events)

        scores = []
        for sig, gt in zip(ds.eval_signals, ds.ground_truth):
            ctx = eng.reconstruct_context({
                "incident_id": sig["incident_id"],
                "ts": sig["ts"],
                "trigger": sig.get("trigger", ""),
                "service": sig.get("service", ""),
            }, mode="fast")
            in_topk, prec = score_match(ctx, gt, k=5)
            rem_ok = score_remediation(ctx, gt)
            scores.append(type("S", (), {
                "incident_id": sig["incident_id"],
                "correct_family_in_top_k": in_topk,
                "precision_at_k": prec,
                "remediation_matches": rem_ok,
                "latency_ms": 0.0,
            }))
        eng.close()
        per.append(aggregate(scores))

    keys = ["recall@5", "precision@5_mean", "remediation_acc"]
    return {k: round(sum(x[k] for x in per) / len(per), 4) for k in keys}


if __name__ == "__main__":
    seeds = [42, 101, 999]
    diag = run_diag(seeds)
    write_outputs(diag, "diag_p02")
    best = sweep(seeds)
    with open("sweep_best.json", "w") as f:
        json.dump(best, f, indent=2)
    print("wrote diag_p02.json diag_p02.csv sweep_best.json")
    print("best", best)
