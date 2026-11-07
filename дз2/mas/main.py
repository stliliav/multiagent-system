import os
import ast
import json
from typing import Dict, Any, TypedDict, Literal
from langchain_community.llms import Ollama
from langgraph.graph import StateGraph, END

# 1. LLM models setup (routing and engine by Ollama)
# Routing principle: light model for classification, heavy model for code generation
router_llm = Ollama(model="mistral:7b", temperature=0.0)
worker_llm = Ollama(model="qwen2.5:7b", temperature=0.1)
validator_llm = Ollama(model="qwen2.5:7b", temperature=0.0)


# 2. System state schema
class AgentState(TypedDict):
    user_query: str
    task_complexity: str  # "simple" | "complex"
    assigned_worker: str
    code_snippet: str
    validation_status: str  # "APPROVED" | "REJECTED"
    validation_feedback: str
    retry_count: int
    final_output: str


# 3. agent skills (Integrated Tools)
def skill_inspect_ast(code: str) -> Dict[str, Any]:

    #Skill file: AST static code inspector and security check.
    #Checks syntax errors and blocks forbidden call executions in sandbox

    try:
        parsed_ast = ast.parse(code)
    except SyntaxError as e:
        return {"valid": False, "error": f"SyntaxError: {e}"}

    forbidden_calls = {"eval", "exec", "__import__", "open"}
    for node in ast.walk(parsed_ast):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in forbidden_calls:
                return {"valid": False, "error": f"Forbidden execution call: {node.func.id}"}

    return {"valid": True, "error": None}


def skill_memory_extractor(query: str) -> Dict[str, str]:
    #Skill file: extracts user preferences/entities for long-term memory

    facts = {}
    if "python" in query.lower():
        facts["language"] = "Python"
    return facts


# 4. Multi-agent nodes (router -> worker -> verifier)

def router_node(state: AgentState) -> AgentState:
    """Router agent (Jev-like model routing for cost optimization)"""
    prompt = f"""
    Classify task complexity.
    Query: {state['user_query']}
    If it requires code generation or multi-step logic, respond ONLY with 'complex'.
    Otherwise respond ONLY with 'simple'
    """
    response = router_llm.invoke(prompt).strip().lower()
    complexity = "complex" if "complex" in response else "simple"

    return {
        **state,
        "task_complexity": complexity,
        "assigned_worker": "Qwen2.5-HeavyCoder" if complexity == "complex" else "Mistral-Light",
        "retry_count": 0
    }


def worker_node(state: AgentState) -> AgentState:
    """worker agent node (Generates Solution)"""
    feedback = state.get("validation_feedback", "")
    feedback_context = f"\nFIX PREVIOUS ERROR: {feedback}" if feedback else ""

    prompt = f"""
    You are an expert Python developer inside a Docker Sandbox container.
    User query: {state['user_query']} {feedback_context}
    Write clean Python code. Return ONLY valid python code enclosed inside ```python ``` block
    """
    response = worker_llm.invoke(prompt)

    code = response
    if "```python" in response:
        code = response.split("```python")[1].split("```")[0].strip()
    elif "```" in response:
        code = response.split("```")[1].split("```")[0].strip()

    return {**state, "code_snippet": code}


def verifier_node(state: AgentState) -> AgentState:
    """verification agent / layer (validates decisions amd code safety)"""
    code = state.get("code_snippet", "")

    # 1. deterministic skill check
    ast_res = skill_inspect_ast(code)
    if not ast_res["valid"]:
        return {
            **state,
            "validation_status": "REJECTED",
            "validation_feedback": ast_res["error"],
            "retry_count": state.get("retry_count", 0) + 1
        }

    # 2. semantic LLM audit
    prompt = f"""
    Audit the code for safety and correctness.
    Query: {state['user_query']}
    Code:
    {code}

    Respond STRICTLY in JSON format:
    {{"status": "APPROVED", "feedback": "OK"}} OR {{"status": "REJECTED", "feedback": "reason"}}
    """
    response = validator_llm.invoke(prompt)
    try:
        parsed = json.loads(response)
        status = parsed.get("status", "REJECTED")
        feedback = parsed.get("feedback", "Invalid execution")
    except Exception:
        status = "APPROVED" if "error" not in response.lower() else "REJECTED"
        feedback = response

    return {
        **state,
        "validation_status": status,
        "validation_feedback": feedback,
        "retry_count": state.get("retry_count", 0) + 1,
        "final_output": code if status == "APPROVED" else ""
    }


# 5. graph routing, compilation with LangGraph framework
def route_next(state: AgentState) -> Literal["worker", "end"]:
    if state["validation_status"] == "APPROVED" or state["retry_count"] >= 3:
        return "end"
    return "worker"


builder = StateGraph(AgentState)
builder.add_node("router", router_node)
builder.add_node("worker", worker_node)
builder.add_node("verifier", verifier_node)

builder.set_entry_point("router")
builder.add_edge("router", "worker")
builder.add_edge("worker", "verifier")
builder.add_conditional_edges("verifier", route_next, {"worker": "worker", "end": END})

graph = builder.compile()

# 6. execution test
if __name__ == "__main__":
    test_query = "Write a python function to compute the n-th Fibonacci number using dp"
    print(f"Executing query: {test_query}\n")

    res = graph.invoke({"user_query": test_query})

    print("=== WORKFLOW RESULTS ===")
    print(f"Task complexity : {res['task_complexity']}")
    print(f"Assigned worker : {res['assigned_worker']}")
    print(f"Validation status: {res['validation_status']}")
    print(f"Retry count     : {res['retry_count']}")
    print(f"\nFinal code output:\n{res['final_output']}")