"""
RAG Evaluation Harness
======================
Scores the running RAG service against a verified eval set. Measures:
  RETRIEVAL  — recall@k, MRR (did the right chunk get retrieved, how high?)
  ANSWER     — faithfulness + correctness (LLM-as-judge)
  REFUSAL    — did unanswerable questions get correctly refused?

This hits the SAME /ask and /search endpoints your app serves, so it measures
the real system end to end. Re-run after any change (reranking, query rewrite,
prompt edits) and compare the printed deltas — that before/after table is the
portfolio centerpiece.

Setup:
    pip install requests openai python-dotenv
    # RAG service running on localhost:8000, OPENAI_API_KEY set

Run:
    python eval_harness.py eval_set.json
    python eval_harness.py eval_set.json --tag baseline       # label this run
    python eval_harness.py eval_set.json --tag with_reranker  # compare later
    python eval_harness.py eval_set.json --no-judge           # retrieval only, no LLM cost
"""
import os
import sys
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import requests
from openai import OpenAI

JUDGE_MODEL = "gpt-4o-mini"
RESULTS_DIR = Path("eval_results")


# ----------------------------- retrieval scoring -----------------------------
def page_match(retrieved_sources, expected_sources) -> bool:
    """A retrieved chunk 'matches' if it shares doc_id AND overlaps an expected page."""
    exp = {(e["doc_id"], p) for e in expected_sources for p in e["pages"]}
    for s in retrieved_sources:
        doc = (s.get("chunk_id", "").split("::")[0])  # doc_id is the chunk_id prefix
        for pg in s.get("pages", []):
            if (doc, pg) in exp:
                return True
    return False


def first_match_rank(retrieved_sources, expected_sources) -> int | None:
    exp = {(e["doc_id"], p) for e in expected_sources for p in e["pages"]}
    for rank, s in enumerate(retrieved_sources):
        doc = s.get("chunk_id", "").split("::")[0]
        for pg in s.get("pages", []):
            if (doc, pg) in exp:
                return rank + 1
    return None


# ----------------------------- LLM judge -----------------------------
def judge_answer(oa, question, expected, answer, context_sources):
    """LLM-as-judge: faithfulness (grounded?) + correctness (matches expected?)."""
    prompt = (
        "You are evaluating a financial-RAG answer. Score two dimensions 0-1.\n"
        "FAITHFULNESS: is the answer consistent with a real annual report and free of "
        "fabricated specifics? (1=fully grounded, 0=hallucinated)\n"
        "CORRECTNESS: does it match the expected answer's key facts/figures? "
        "(1=matches, 0=wrong/missing). If expected says 'FILL', score correctness null.\n"
        "Respond ONLY JSON: {\"faithfulness\": float, \"correctness\": float|null, "
        "\"reason\": str}\n\n"
        f"QUESTION: {question}\n"
        f"EXPECTED: {expected}\n"
        f"ACTUAL ANSWER: {answer}\n"
    )
    try:
        r = oa.chat.completions.create(
            model=JUDGE_MODEL, max_tokens=300,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}])
        return json.loads(r.choices[0].message.content)
    except Exception as e:
        return {"faithfulness": None, "correctness": None, "reason": f"judge_error: {e}"}


def judge_refusal(oa, question, answer):
    """Did the model correctly refuse / flag an unanswerable question?"""
    prompt = (
        "A question was asked that CANNOT be answered from an annual report (data not "
        "disclosed, wrong time period, or a metric inapplicable to the company type). "
        "Did the answer correctly decline or flag this, WITHOUT inventing figures?\n"
        "Respond ONLY JSON: {\"refused_correctly\": true|false, \"reason\": str}\n\n"
        f"QUESTION: {question}\nANSWER: {answer}\n")
    try:
        r = oa.chat.completions.create(
            model=JUDGE_MODEL, max_tokens=200,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}])
        return json.loads(r.choices[0].message.content)
    except Exception as e:
        return {"refused_correctly": None, "reason": f"judge_error: {e}"}


# ----------------------------- API calls -----------------------------
def call_search(base, question, filters, k):
    body = {"question": question, "top_k": k, **filters}
    r = requests.post(f"{base}/search", json=body, timeout=60)
    r.raise_for_status()
    return r.json()["results"]


def call_ask(base, question, filters):
    body = {"question": question, **filters}
    r = requests.post(f"{base}/ask", json=body, timeout=120)
    r.raise_for_status()
    return r.json()


# ----------------------------- main eval loop -----------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("eval_set", type=Path)
    ap.add_argument("--tag", default="run", help="label for this run (e.g. baseline)")
    ap.add_argument("--no-judge", action="store_true", help="retrieval metrics only")
    args = ap.parse_args()

    spec = json.loads(args.eval_set.read_text(encoding="utf-8"))
    cfg = spec.get("config", {})
    base = cfg.get("api_base", "http://localhost:8000")
    k = cfg.get("retrieval_k", 6)
    questions = spec["questions"]

    oa = None if args.no_judge else OpenAI()

    # quick liveness check
    try:
        ready = requests.get(f"{base}/health/ready", timeout=10).json()
        print(f"Service ready: {ready.get('chunks')} chunks indexed\n")
    except Exception as e:
        print(f"Service not reachable at {base}: {e}")
        sys.exit(1)

    per_q, agg = [], {
        "answerable": 0, "recall_hits": 0, "mrr_sum": 0.0,
        "faith_sum": 0.0, "faith_n": 0, "corr_sum": 0.0, "corr_n": 0,
        "refusal_total": 0, "refusal_correct": 0,
    }

    for q in questions:
        qid, cat = q["id"], q["category"]
        filters = q.get("filters", {})
        should_refuse = q.get("should_refuse", False)
        print(f"[{qid}] ({cat}) {q['question'][:60]}...")

        row = {"id": qid, "category": cat, "should_refuse": should_refuse}

        # --- retrieval (skip recall scoring for refusal questions; no gold chunk) ---
        retrieved = call_search(base, q["question"], filters, k)
        if not should_refuse and q.get("expected_sources"):
            hit = page_match(retrieved, q["expected_sources"])
            rank = first_match_rank(retrieved, q["expected_sources"])
            agg["answerable"] += 1
            agg["recall_hits"] += int(hit)
            agg["mrr_sum"] += (1.0 / rank) if rank else 0.0
            row.update({"recall_hit": hit, "first_rank": rank})
            print(f"     retrieval: {'HIT' if hit else 'MISS'} "
                  f"(rank {rank if rank else '-'})")

        # --- answer ---
        ask = call_ask(base, q["question"], filters)
        answer = ask["answer"]
        row["answer_preview"] = answer[:200]

        if not args.no_judge:
            if should_refuse:
                j = judge_refusal(oa, q["question"], answer)
                agg["refusal_total"] += 1
                ok = bool(j.get("refused_correctly"))
                agg["refusal_correct"] += int(ok)
                row["refused_correctly"] = ok
                print(f"     refusal: {'OK' if ok else 'FAILED — possible hallucination'}")
            else:
                j = judge_answer(oa, q["question"], q.get("expected_answer", ""),
                                 answer, retrieved)
                f, c = j.get("faithfulness"), j.get("correctness")
                if f is not None:
                    agg["faith_sum"] += f; agg["faith_n"] += 1
                if c is not None:
                    agg["corr_sum"] += c; agg["corr_n"] += 1
                row.update({"faithfulness": f, "correctness": c,
                            "judge_reason": j.get("reason", "")})
                print(f"     answer: faith={f} corr={c}")
        per_q.append(row)
        print()

    # ----------------------------- report -----------------------------
    n_ans = max(1, agg["answerable"])
    report = {
        "tag": args.tag,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "n_questions": len(questions),
        "retrieval": {
            "answerable_questions": agg["answerable"],
            "recall_at_k": round(agg["recall_hits"] / n_ans, 3),
            "mrr": round(agg["mrr_sum"] / n_ans, 3),
        },
        "answer": {
            "faithfulness": round(agg["faith_sum"] / max(1, agg["faith_n"]), 3)
                            if agg["faith_n"] else None,
            "correctness": round(agg["corr_sum"] / max(1, agg["corr_n"]), 3)
                           if agg["corr_n"] else None,
        },
        "refusal": {
            "total": agg["refusal_total"],
            "correct": agg["refusal_correct"],
            "accuracy": round(agg["refusal_correct"] / max(1, agg["refusal_total"]), 3)
                        if agg["refusal_total"] else None,
        },
        "per_question": per_q,
    }

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = RESULTS_DIR / f"{args.tag}_{stamp}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("=" * 60)
    print(f"RESULTS  [{args.tag}]")
    print("=" * 60)
    print(f"Retrieval  recall@{k}: {report['retrieval']['recall_at_k']}   "
          f"MRR: {report['retrieval']['mrr']}   "
          f"(n={agg['answerable']})")
    if not args.no_judge:
        print(f"Answer     faithfulness: {report['answer']['faithfulness']}   "
              f"correctness: {report['answer']['correctness']}")
        print(f"Refusal    accuracy: {report['refusal']['accuracy']}   "
              f"({agg['refusal_correct']}/{agg['refusal_total']})")
    print(f"\nSaved -> {out}")
    print("Re-run with a different --tag after each upgrade to compare.")


if __name__ == "__main__":
    main()
