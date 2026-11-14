---
name: docker_sandbox_runner
description: Ephemeral, network-isolated Docker container execution with strict memory/CPU caps.
---
# Isolation policy
- Image: `python:3.14-slim`
- Flags: `--rm --network none --memory 256m --cpus 1.0`
- Timeout: 5 seconds hard kill against infinite loops.
