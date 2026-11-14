import ast
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict


def load_system_context() -> str:
    #читает SOUL.md и все .md файлы скиллов в единый системный промпт
    blocks = []
    if os.path.exists("SOUL.md"):
        blocks.append(Path("SOUL.md").read_text(encoding="utf-8"))

    skills_dir = Path("skills")
    if skills_dir.exists():
        for skill_path in sorted(skills_dir.glob("*.md")):
            blocks.append(f"### Skill: {skill_path.stem}\n" + skill_path.read_text(encoding="utf-8"))

    return "\n\n---\n\n".join(blocks)


def run_ast_skill(code: str) -> Dict[str, Any]:
    #скилл 1: статическая проверка синтаксиса и безопасности через AST
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {"valid": False, "error": f"SyntaxError (line {e.lineno}): {e.msg}"}

    forbidden_calls = {"eval", "exec", "compile", "__import__", "open", "input"}
    forbidden_modules = {"os", "sys", "subprocess", "socket", "urllib", "requests", "shutil", "pathlib"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in forbidden_modules:
                    return {"valid": False, "error": f"Forbidden import: {alias.name}"}
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in forbidden_modules:
                return {"valid": False, "error": f"Forbidden from-import: {node.module}"}
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in forbidden_calls:
                return {"valid": False, "error": f"Forbidden call: {node.func.id}"}

    return {"valid": True, "error": None}


def run_sandbox_skill(code: str, timeout: int = 5) -> Dict[str, Any]:
    #скилл 2: запуск кода в изолированном Docker-контейнере без сети через stdin
    cmd = [
        "docker", "run", "--rm", "-i",
        "--network", "none",
        "--memory", "256m",
        "--cpus", "1.0",
        "python:3.14-slim",
        "python", "-",
    ]

    try:
        res = subprocess.run(cmd, input=code, capture_output=True, text=True, timeout=timeout)
        return {
            "success": res.returncode == 0,
            "stdout": res.stdout.strip(),
            "stderr": res.stderr.strip(),
            "exit_code": res.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": "Execution timeout (infinite loop)", "exit_code": -1}
    except Exception as e:
        return {"success": False, "stdout": "", "stderr": f"Sandbox error: {e}", "exit_code": -1}