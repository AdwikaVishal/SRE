# Anvil P-02 — Persistent Context Engine

A system that recognizes the same infrastructure failure even after services have been renamed, topology has changed, and telemetry looks different.

## Quickstart

```bash
# 1. Clone and enter
git clone <repo> && cd <repo>

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run self-check (produces report.json)
python self_check.py --adapter adapters.engine:Engine --quick
```

Should produce a score output in under 60 seconds on a clean machine.

## Architecture

Four layers, each with one job:

| Layer | Component | Job |
|-------|-----------|-----|
| 1 | `IdentityResolver` | Canonical IDs across renames |
| 2 | `EventStore` (DuckDB) | Temporal append-only event log |
| 3 | `OperationalGraph` (NetworkX) | Probabilistic causal graph |
| 4 | `ContextAssembler` | Fast/deep context reconstruction |

## Key design decisions

**Entity resolution on rename events** — When a `topology kind=rename` event arrives, `IdentityResolver.rename()` maps the old name to the same canonical ID. Every downstream component uses canonical IDs, never raw service names.

**Causal chain with enforced temporal ordering** — `OperationalGraph.add_edge()` asserts `ts_src < ts_dst` at write time. Inverted edges are silently dropped.

**LLM budget for fast mode** — Fast mode (`≤ 2s p95`) uses zero LLM calls. All work is in-process: DuckDB window query, NetworkX BFS, motif Jaccard scan, template string. Deep mode makes exactly one LLM call.

## Running full benchmark

```bash
# Multi-seed fast mode
python self_check.py --adapter adapters.engine:Engine

# Docker
docker build -t anvil-p02 .
docker run --rm anvil-p02
```

## Environment variables

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | Enables deep mode via GPT-4o-mini |
| `OPENAI_MODEL` | Override model (default: gpt-4o-mini) |
| `ANTHROPIC_API_KEY` | Enables deep mode via Claude Haiku |
| `ANTHROPIC_MODEL` | Override model |
