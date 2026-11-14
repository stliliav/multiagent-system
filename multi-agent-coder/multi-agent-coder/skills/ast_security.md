---
name: ast_security_inspector
description: Static AST analysis blocking syntax errors, dangerous builtins, and system/network imports.
---
# Rules
1. Parse code via `ast.parse`; reject immediately on `SyntaxError`.
2. Forbid builtins: `eval`, `exec`, `compile`, `__import__`, `open`, `input`.
3. Forbid modules: `os`, `sys`, `subprocess`, `socket`, `urllib`, `requests`, `shutil`, `pathlib`.
