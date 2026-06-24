# Evaluation — retrieval & answer quality harness

Measures whether the system actually works, and quantifies the impact of design
choices (notably reranking). RAG without evaluation is guesswork; this is how the
pipeline's quality is verified and tuned.

## Files

| File | Purpose |
|------|---------|
| `eval_set.json` | Curated question/answer pairs with ground-truth source pages |
| `eval_harness.py` | Runs the set against the API, scores the results |

## Metrics

| Metric | What it measures |
|--------|------------------|
| **Recall@k** | Did the correct source chunk appear in the top-k retrieved? |
| **MRR** | How highly was the correct source ranked? (rank quality) |
| **Faithfulness** | Is the answer grounded in the retrieved context? (LLM judge) |
| **Refusal accuracy** | Does it correctly decline when the answer isn't in the corpus? |

## Question categories

The eval set spans the failure modes that matter for financial QA:

- `point_lookup` — single figure from a known report ("profit after tax 2024")
- `table_computation` — a ratio/derivation needing actual table figures
- `refusal_trap` — answer genuinely not in the corpus; should decline, not invent
- `cross_sector_premise` — a false or cross-company premise to test robustness

## Usage

```bash
# baseline (no reranking)
python eval_harness.py --tag baseline

# with reranking enabled on the API
python eval_harness.py --tag with_reranker

# compare the two runs
python eval_harness.py --compare baseline with_reranker
```

The `--tag` runs let you measure the **reranking lift** — the before/after
recall@k and MRR delta — which is the headline quality result for the system.

## Design note

The eval set deliberately includes **refusal traps**. A financial assistant that
confidently fabricates a figure is worse than one that says "not in the reports."
Measuring refusal accuracy, not just answer accuracy, is what keeps the system
trustworthy.
