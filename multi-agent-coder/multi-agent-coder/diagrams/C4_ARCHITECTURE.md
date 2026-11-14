# Архитектурные диаграммы C4 и Sequence Flow

## 1. C1: System Context Diagram
```mermaid
C4Context
    title C1: System Context Diagram — Multi-Agent Code Verification System

    Person(user, "Developer / User", "Отправляет задачу на генерацию и проверку Python-кода")
    System(agent_sys, "Multi-Agent Code System", "Оркестрирует роутинг, генерацию кода, AST/Docker проверку и Jev-оценку")
    System_Ext(ollama, "Ollama Inference Engine", "Локальный хостинг моделей: mistral:7b, qwen2.5:7b, llama3")
    System_Ext(docker_daemon, "Docker Engine", "Изолированный запуск кода в эфемерном контейнере (--network none)")
    System_Ext(webhook, "Alerting Webhook", "Внешний канал оповещений при сбоях и нарушениях AST")

    Rel(user, agent_sys, "Передаёт промпт / получает верифицированный код", "CLI")
    Rel(agent_sys, ollama, "Выполняет инференс агентов", "HTTP :11434")
    Rel(agent_sys, docker_daemon, "Запускает изолированные контейнеры", "Docker Socket")
    Rel(agent_sys, webhook, "Отправляет алерты при инцидентах", "HTTPS POST")
```

## 2. C2: Container Diagram
```mermaid
C4Container
    title C2: Container Diagram — Runtime & Isolation Boundaries

    Person(user, "Developer", "Пользователь системы")

    Container_Boundary(sys, "Multi-Agent Platform") {
        Container(orchestrator, "LangGraph Orchestrator", "Python 3.14, LangGraph", "Управляет графом состояний, роутингом, памятью и трейсами")
        ContainerDb(sqlite_db, "SQLite Memory & Checkpoints", "SQLite 3", "Хранит short-term чекпоинты (checkpoints.db) и long-term эпизоды (episodic_memory.db)")
        Container(sandbox, "Ephemeral Code Sandbox", "Docker (python:3.14-slim)", "Изолированный контейнер без сети (--network none, 256MB RAM)")
        Container(trace_logs, "Observability Storage", "JSON Log File (agent_traces.log)", "Хранит спаны, метрики задержек и события алертов")
    }

    System_Ext(ollama, "Ollama Runtime", "Локальный сервер инференса 3 LLM")

    Rel(user, orchestrator, "Запускает main.py / evals.py")
    Rel(orchestrator, ollama, "Запросы на роутинг, синтез и судейство", "HTTP :11434")
    Rel(orchestrator, sqlite_db, "Читает/пишет состояние треда и прошлые эпизоды", "SQL")
    Rel(orchestrator, sandbox, "Выполняет сгенерированный скрипт", "docker run --rm")
    Rel(orchestrator, trace_logs, "Пишет структурированные логи и метрики", "File I/O")
```

## 3. C3: Component Diagram
```mermaid
C4Component
    title C3: Component Diagram — LangGraph Orchestrator Internals

    Container_Boundary(orch, "LangGraph Orchestrator (main.py & skills.py)") {
        Component(router, "Cost-Aware Router Node", "mistral:7b", "Классифицирует сложность задачи (simple / complex)")
        Component(worker, "Adaptive Worker Node", "mistral:7b / qwen2.5:7b", "Генерирует код и исправляет ошибки по фидбеку с эскалацией модели")
        Component(skills_exec, "Deterministic Verifier & Skills", "skills.py (AST + Docker CLI)", "Читает SOUL.md / skills/*.md, проверяет AST и запускает sandbox")
        Component(judge, "Jev-like Judge Node", "llama3", "Оценивает корректность, edge cases и выставляет score >= 0.8")
        Component(mem_mgr, "Hybrid Memory Manager", "SqliteSaver + EpisodicMemory", "Подгружает релевантный опыт и сохраняет одобренные решения")
        Component(obs, "Observability & Alerting", "logging + urllib webhook", "Замеряет latency, агрегирует METRICS и шлёт алерты")
    }

    Rel(router, mem_mgr, "Запрашивает прошлые уроки по категории")
    Rel(router, worker, "Передаёт задачу и выбранную модель")
    Rel(worker, skills_exec, "Передаёт код на детерминированную проверку")
    Rel(skills_exec, judge, "Передаёт stdout и код после успеха sandbox")
    Rel(judge, mem_mgr, "Сохраняет одобренный эпизод (score >= 0.8)")
    Rel(judge, worker, "Возвращает на ретрай (если score < 0.8)")
    Rel(skills_exec, obs, "Логирует спаны и вызывает trigger_alert()")
```

## 4. C4: Code / Class Diagram
```mermaid
classDiagram
    class AgentState {
        +str user_query
        +str task_complexity
        +str assigned_model
        +str memory_context
        +str code_snippet
        +str sandbox_stdout
        +str validation_status
        +float judge_score
        +int retry_count
        +str final_output
    }

    class EpisodicMemory {
        -str db_path
        +save(query, complexity, score, lessons)
        +retrieve(query, complexity, limit) str
    }

    class SkillsModule {
        +load_system_context() str
        +run_ast_skill(code) dict
        +run_sandbox_skill(code, timeout) dict
    }

    class ObservabilityModule {
        +dict METRICS
        +log_span(node, duration, meta)
        +trigger_alert(title, details)
    }

    AgentState --> EpisodicMemory : обогащается контекстом
    AgentState --> SkillsModule : проверяется скиллами
    SkillsModule --> ObservabilityModule : пишет спаны и алерты
```

## 5. C4 Deployment Diagram
```mermaid
C4Deployment
    title C4: Deployment Diagram — Host & Docker Sandbox Isolation

    Deployment_Node(host, "Host Machine", "Windows or Linux + Docker") {
        Deployment_Node(ollama_proc, "Ollama Process", "Port 11434") {
            Container(models, "LLM Weights", "GGUF", "mistral 7b, qwen2.5 7b, llama3")
        }

        Deployment_Node(orch_zone, "Orchestrator Container", "Docker multi-agent-coder") {
            Container(orch_app, "Python Runtime", "main.py and skills.py", "Выполняет граф LangGraph и хранит data")
        }

        Deployment_Node(isolated_zone, "Zero-Trust Sandbox Zone", "Docker Isolated Network None") {
            Container(sbx_cont, "ephemeral-runner", "python 3.14-slim", "Read-only скрипт, 256MB RAM, 1 CPU, удаление после запуска")
        }
    }

    Rel(orch_app, models, "REST API", "host.docker.internal:11434")
    Rel(orch_app, sbx_cont, "Spawns via Docker Socket", "Isolated IPC")
```

## 6. C4 Dynamic Diagram — Sequence Flow
```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Router as Router (mistral:7b)
    participant Memory as EpisodicMemory (SQLite)
    participant Worker as Worker (mistral:7b / qwen2.5:7b)
    participant Skills as Verifier (AST + Docker Sandbox)
    participant Judge as Jev Judge (llama3)

    User->>Router: user_query ("Binary search...")
    Router->>Memory: retrieve(query, complexity)
    Memory-->>Router: Релевантные прошлые эпизоды и уроки
    Router->>Worker: Маршрутизация (simple -> mistral:7b, complex -> qwen2.5:7b)

    loop Максимум 3 попытки (retry_count < 3)
        Worker->>Skills: Сгенерированный Python-код
        Skills->>Skills: 1. run_ast_skill() [skills/ast_security.md]
        alt Ошибка AST или Sandbox
            Skills-->>Worker: REJECTED + ошибка (эскалация на qwen2.5:7b)
        else Sandbox пройден (exit_code == 0)
            Skills->>Judge: Код + sandbox_stdout
            Judge->>Judge: Оценка по рубрике (0.0 - 1.0)
            alt score < 0.8
                Judge-->>Worker: REJECTED + замечания судьи
            else score >= 0.8
                Judge->>Memory: save(query, complexity, score, lessons)
                Judge-->>User: APPROVED + верифицированный код
            end
        end
    end
```

## 7. C4 Data & Memory Architecture Diagram
```mermaid
flowchart LR
    subgraph ShortTerm["Short-Term Memory (Session / Thread)"]
        CP[LangGraph SqliteSaver<br/>data/checkpoints.db]
        ST[AgentState: retry_count, stderr, AST errors]
        CP <--> ST
    end

    subgraph LongTerm["Long-Term Memory (Cross-Session Episodic)"]
        SQL[(SQLite<br/>data/episodic_memory.db)]
        RANK[Token Overlap + Category Bonus + Judge Score Ranking]
        SQL --> RANK
    end

    Query[User Query] --> Router
    RANK -->|Top-3 релевантных урока| Router
    Router --> Worker
    Worker --> Verifier
    Verifier --> Judge
    Judge -->|APPROVED & score >= 0.8| SQL
```
