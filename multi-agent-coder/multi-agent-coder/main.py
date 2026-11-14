import os
import json
import time
import sqlite3
import logging
import urllib.request
from datetime import datetime, timezone
from typing import List, Literal, TypedDict

from langchain_ollama import ChatOllama
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from skills import load_system_context, run_ast_skill, run_sandbox_skill

os.makedirs("data", exist_ok=True)
OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")

# 1. OBSERVABILITY (structured logs, metrics, traces, webhook alerting)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler("data/agent_traces.log", encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger("multi-agent")
METRICS = {"runs_total": 0, "approved": 0, "failed": 0, "retries": 0, "avg_judge_score": 0.0}


def log_span(node: str, duration: float, meta: dict) -> None:
    logger.info(json.dumps({"event": "span", "node": node, "latency_sec": round(duration, 3), **meta}, ensure_ascii=False))


def trigger_alert(title: str, details: str) -> None:
    logger.error(json.dumps({"event": "ALERT", "title": title, "details": details}, ensure_ascii=False))
    webhook = os.getenv("ALERT_WEBHOOK_URL")
    if webhook:
        req = urllib.request.Request(
            webhook,
            data=json.dumps({"text": f"{title}: {details}"}).encode("utf-8"),
            headers={"Content-type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=3)
        except Exception as e:
            logger.warning(f"Alert webhook failed: {e}")


# 2.long term episodical memory (SQLite temporal episodes)
class EpisodicMemory:
    def __init__(self, db_path: str = "data/episodic_memory.db"):
        self.db_path = db_path
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT,
                    complexity TEXT,
                    judge_score REAL,
                    lessons TEXT,
                    created_at TEXT
                )
            """)

    def save(self, query: str, complexity: str, score: float, lessons: List[str]) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO episodes (query, complexity, judge_score, lessons, created_at) VALUES (?, ?, ?, ?, ?)",
                (query, complexity, score, "; ".join(lessons), datetime.now(timezone.utc).isoformat()),
            )

    def retrieve(self, query: str, complexity: str, limit: int = 3) -> str:
        tokens = set(query.lower().split())
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT query, complexity, judge_score, lessons FROM episodes").fetchall()

        scored = []
        for q, comp, score, lessons in rows:
            overlap = len(tokens & set(q.lower().split())) / max(len(tokens), 1)
            total = overlap + (0.25 if comp == complexity else 0.0) + score * 0.2
            if total > 0.3:
                scored.append((total, f"- Past task: `{q}` (score={score}) | Lessons: {lessons or 'none'}"))

        scored.sort(reverse=True)
        return "\n".join([item[1] for item in scored[:limit]]) or "No relevant past episodes"


# 3. 3 routing models by costs and roles
# - mistral:7b   -> Быстрый Router + Fast Worker для простых задач
# - qwen2.5:7b   -> Deep Coder для сложных алгоритмов и исправления ошибок
# - llama3       -> Независимый Jev-like Judge для итоговой оценки решения

fast_llm = ChatOllama(model="mistral:7b", base_url=OLLAMA_URL, temperature=0.0)
deep_coder_llm = ChatOllama(model="qwen2.5:7b", base_url=OLLAMA_URL, temperature=0.1)
judge_llm = ChatOllama(model="llama3", base_url=OLLAMA_URL, temperature=0.0)

SYSTEM_PROMPT = load_system_context()
ltm = EpisodicMemory()
short_term_memory = SqliteSaver(sqlite3.connect("data/checkpoints.db", check_same_thread=False))


class AgentState(TypedDict):
    user_query: str
    task_complexity: str
    assigned_model: str
    memory_context: str
    code_snippet: str
    sandbox_stdout: str
    validation_status: str
    validation_feedback: str
    judge_score: float
    judge_reasoning: str
    lessons_learned: List[str]
    retry_count: int
    final_output: str


# 4.  nodes of multiagent graph
def router_node(state: AgentState) -> AgentState:
    t0 = time.perf_counter()
    prompt = (
        "Classify the task into 'simple' (basic syntax/one-liner) or 'complex' (algorithms, edge cases, data structures).\n"
        f"Query: {state['user_query']}\n"
        "Respond with ONE word: simple or complex."
    )
    resp = fast_llm.invoke([HumanMessage(content=prompt)]).content.strip().lower()
    complexity = "complex" if "complex" in resp else "simple"
    model = "qwen2.5:7b" if complexity == "complex" else "mistral:7b"
    mem = ltm.retrieve(state["user_query"], complexity)

    log_span("router", time.perf_counter() - t0, {"complexity": complexity, "routed_to": model})
    return {
        **state,
        "task_complexity": complexity,
        "assigned_model": model,
        "memory_context": mem,
        "retry_count": 0,
        "lessons_learned": [],
    }


def worker_node(state: AgentState) -> AgentState:
    t0 = time.perf_counter()
    use_deep = state["task_complexity"] == "complex" or state.get("retry_count", 0) > 0
    llm = deep_coder_llm if use_deep else fast_llm
    model_name = "qwen2.5:7b" if use_deep else "mistral:7b"

    feedback = state.get("validation_feedback", "")
    fix_hint = f"\nPREVIOUS ERROR TO FIX:\n{feedback}" if feedback else ""

    prompt = (
        f"Relevant memory:\n{state['memory_context']}\n\n"
        f"Task: {state['user_query']}{fix_hint}\n"
        "Write clean Python code with minimal `assert` self-tests at the bottom.\n"
        "Return ONLY valid Python code inside ```python ... ```."
    )
    raw = llm.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)]).content
    code = raw.split("```python")[1].split("```")[0].strip() if "```python" in raw else raw.strip("` \n")

    log_span("worker", time.perf_counter() - t0, {"model": model_name, "attempt": state.get("retry_count", 0) + 1})
    return {**state, "assigned_model": model_name, "code_snippet": code}


def verifier_node(state: AgentState) -> AgentState:
    # детерминированная верификация скиллами. AST и Docker Sandbox
    t0 = time.perf_counter()
    code = state["code_snippet"]
    retries = state.get("retry_count", 0) + 1
    lessons = list(state.get("lessons_learned", []))

    ast_res = run_ast_skill(code)
    if not ast_res["valid"]:
        METRICS["retries"] += 1
        trigger_alert("AST Security Violation", f"{state['user_query']} -> {ast_res['error']}")
        lessons.append(f"Avoid {ast_res['error']}")
        log_span("verifier", time.perf_counter() - t0, {"status": "AST_REJECTED", "error": ast_res["error"]})
        return {
            **state,
            "validation_status": "REJECTED",
            "validation_feedback": ast_res["error"],
            "lessons_learned": lessons,
            "retry_count": retries,
        }

    sbx_res = run_sandbox_skill(code)
    if not sbx_res["success"]:
        METRICS["retries"] += 1
        lessons.append(f"Runtime fix: {sbx_res['stderr'][:70]}")
        log_span("verifier", time.perf_counter() - t0, {"status": "SANDBOX_REJECTED", "stderr": sbx_res["stderr"]})
        return {
            **state,
            "validation_status": "REJECTED",
            "validation_feedback": f"Runtime Error: {sbx_res['stderr']}",
            "lessons_learned": lessons,
            "retry_count": retries,
        }

    log_span("verifier", time.perf_counter() - t0, {"status": "SANDBOX_PASSED"})
    return {**state, "validation_status": "SANDBOX_PASSED", "sandbox_stdout": sbx_res["stdout"], "retry_count": retries}


def judge_node(state: AgentState) -> AgentState:
    #Jev-like Judge (llama3): оценка качества решения по рубрике (порог >= 0.8)
    t0 = time.perf_counter()
    prompt = (
        "You are an impartial Jev-like Code Judge.\n"
        f"Requirement: {state['user_query']}\n"
        f"Code:\n```python\n{state['code_snippet']}\n```\n"
        f"Sandbox stdout: {state.get('sandbox_stdout', '')}\n\n"
        "Score from 0.0 to 1.0 based on correctness, edge cases, and code clarity.\n"
        'Respond STRICTLY in JSON: {"score": 0.9, "approved": true, "reasoning": "..."}'
    )
    raw = judge_llm.invoke([HumanMessage(content=prompt)]).content
    try:
        data = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
        score = float(data.get("score", 0.0))
        approved = bool(data.get("approved", False)) and score >= 0.8
        reasoning = data.get("reasoning", "OK")
    except Exception:
        score, approved, reasoning = 0.5, False, f"Invalid judge JSON: {raw[:100]}"

    METRICS["runs_total"] += 1
    METRICS["avg_judge_score"] = round(
        (METRICS["avg_judge_score"] * (METRICS["runs_total"] - 1) + score) / METRICS["runs_total"], 3
    )
    log_span("judge", time.perf_counter() - t0, {"score": score, "approved": approved})

    if approved:
        METRICS["approved"] += 1
        ltm.save(state["user_query"], state["task_complexity"], score, state.get("lessons_learned", []))
        return {
            **state,
            "validation_status": "APPROVED",
            "judge_score": score,
            "judge_reasoning": reasoning,
            "final_output": state["code_snippet"],
        }

    METRICS["retries"] += 1
    lessons = list(state.get("lessons_learned", [])) + [reasoning[:70]]
    return {
        **state,
        "validation_status": "REJECTED",
        "validation_feedback": f"Judge rejected (score={score}): {reasoning}",
        "judge_score": score,
        "judge_reasoning": reasoning,
        "lessons_learned": lessons,
    }


# 5. routing and graph bulidinf

def route_after_verifier(state: AgentState) -> Literal["judge", "worker", "end"]:
    if state["validation_status"] == "SANDBOX_PASSED":
        return "judge"
    if state["retry_count"] >= 3:
        METRICS["failed"] += 1
        trigger_alert("Max retries reached", f"Query '{state['user_query']}' failed: {state['validation_feedback']}")
        return "end"
    return "worker"


def route_after_judge(state: AgentState) -> Literal["worker", "end"]:
    if state["validation_status"] == "APPROVED":
        return "end"
    if state["retry_count"] >= 3:
        METRICS["failed"] += 1
        trigger_alert("Judge rejected final attempt", f"Query '{state['user_query']}': {state['judge_reasoning']}")
        return "end"
    return "worker"


builder = StateGraph(AgentState)
builder.add_node("router", router_node)
builder.add_node("worker", worker_node)
builder.add_node("verifier", verifier_node)
builder.add_node("judge", judge_node)

builder.set_entry_point("router")
builder.add_edge("router", "worker")
builder.add_edge("worker", "verifier")
builder.add_conditional_edges("verifier", route_after_verifier, {"judge": "judge", "worker": "worker", "end": END})
builder.add_conditional_edges("judge", route_after_judge, {"worker": "worker", "end": END})

graph = builder.compile(checkpointer=short_term_memory)

if __name__ == "__main__":
    test_query = "Write a Python function for binary search handling empty lists and duplicates."
    res = graph.invoke({"user_query": test_query}, config={"configurable": {"thread_id": "demo-1"}})

    print(f"Complexity  : {res['task_complexity']}")
    print(f"Worker used : {res['assigned_model']}")
    print(f"Status      : {res['validation_status']}")
    print(f"Judge score : {res.get('judge_score', 0.0)} ({res.get('judge_reasoning', '')})")
    print(f"Metrics     : {METRICS}")
    print(f"\nFinal code:\n{res.get('final_output', 'FAILED')}")
