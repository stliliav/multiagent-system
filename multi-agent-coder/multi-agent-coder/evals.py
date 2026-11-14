import json
import time
from main import graph, METRICS

DATASET = [
    {"id": "t1", "query": "Write a function to check if a string is a palindrome.", "expected_complexity": "simple"},
    {"id": "t2", "query": "Implement binary search returning the first occurrence index.", "expected_complexity": "complex"},
    {"id": "t3", "query": "Implement topological sort for a directed acyclic graph.", "expected_complexity": "complex"},
]


def run_evals():
    report = []
    for item in DATASET:
        t0 = time.perf_counter()
        out = graph.invoke(
            {"user_query": item["query"]},
            config={"configurable": {"thread_id": f"eval-{item['id']}"}},
        )
        passed = out["validation_status"] == "APPROVED" and out.get("judge_score", 0.0) >= 0.8
        report.append({
            "id": item["id"],
            "routing_ok": out["task_complexity"] == item["expected_complexity"],
            "passed": passed,
            "judge_score": out.get("judge_score", 0.0),
            "retries": out["retry_count"],
            "latency_sec": round(time.perf_counter() - t0, 2),
        })

    summary = {
        "pass_rate": round(sum(r["passed"] for r in report) / len(report), 2),
        "routing_accuracy": round(sum(r["routing_ok"] for r in report) / len(report), 2),
        "system_metrics": METRICS,
        "cases": report,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    run_evals()
