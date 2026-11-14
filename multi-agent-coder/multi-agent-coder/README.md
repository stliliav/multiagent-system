# Multi-Agent Code Verification System

## Запуск через Docker
```bash
docker build -t multi-agent-coder .
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v ${PWD}/data:/app/data multi-agent-coder
```

## Запуск Evals
```bash
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v ${PWD}/data:/app/data multi-agent-coder python evals.py
```

