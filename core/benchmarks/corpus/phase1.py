"""Phase 1 corpus: the same semantic payload in competing representations.

Each item: category, then a dict of representation -> text. All texts were
authored to preserve the full instruction semantics of `raw_en` (the
reference). `foldlang_alias` assumes a session dictionary is established;
its bootstrap overhead is accounted separately by the runner.
"""

CORPUS = [
    {
        "id": "code_review",
        "category": "coding",
        "reps": {
            "raw_en": "Please analyze the following Python program, identify any bugs you can find, fix all of them, make sure you preserve the existing functionality, and then explain each of the changes you made.",
            "concise_en": "Analyze this Python program: find and fix all bugs, preserve existing functionality, explain each change.",
            "terse_en": "Py: find+fix all bugs, keep behavior, explain each change.",
            "raw_zh": "请分析以下Python程序，找出所有你能发现的错误并全部修复，确保保留现有功能，然后解释你所做的每一项更改。",
            "concise_zh": "分析此Python程序：找出并修复所有错误，保留现有功能，解释每项改动。",
            "foldlang_sym": "@py.anlz bugs+fixall preserve=1 explain=each",
            "foldlang_alias": "K1 py: bugs fixall K2 explain-each",
        },
    },
    {
        "id": "agent_guard",
        "category": "agent_instruction",
        "reps": {
            "raw_en": "While making these changes, please do not break any existing functionality, run the full test suite afterwards, preserve all public APIs exactly as they are, update the documentation to match, and do not modify any files that are unrelated to this task.",
            "concise_en": "Do not break existing functionality; run full tests after; preserve all public APIs exactly; update docs to match; do not modify unrelated files.",
            "terse_en": "No breakage; run tests; keep public APIs; update docs; touch only related files.",
            "raw_zh": "在进行这些更改时，请不要破坏任何现有功能，之后运行完整的测试套件，完全保留所有公共API，更新文档以保持一致，并且不要修改与此任务无关的任何文件。",
            "concise_zh": "不要破坏现有功能；之后运行全部测试；完全保留公共API；同步更新文档；不要修改无关文件。",
            "foldlang_sym": "!break=0 tests=full api=keep docs=sync files=scope-only",
            "foldlang_alias": "K3 K4 K5 K6 K7",
        },
    },
    {
        "id": "research",
        "category": "research",
        "reps": {
            "raw_en": "I would like you to research the current state of the art in retrieval-augmented generation, compare at least three different approaches, discuss their trade-offs in terms of latency, accuracy, and cost, and summarize your findings in a structured report with citations.",
            "concise_en": "Research state-of-the-art retrieval-augmented generation: compare at least 3 approaches, discuss latency/accuracy/cost trade-offs, summarize as a structured cited report.",
            "terse_en": "Survey SOTA RAG: >=3 approaches, latency/accuracy/cost trade-offs, structured cited report.",
            "raw_zh": "我希望你研究检索增强生成的最新技术水平，比较至少三种不同的方法，讨论它们在延迟、准确性和成本方面的权衡，并以带引用的结构化报告总结你的发现。",
            "concise_zh": "调研RAG最新进展：比较至少3种方法，讨论延迟、准确性、成本权衡，输出带引用的结构化报告。",
            "foldlang_sym": "rag.survey n>=3 tradeoffs={lat,acc,cost} out=report+cites",
            "foldlang_alias": "K8 rag n>=3 lat/acc/cost K9",
        },
    },
    {
        "id": "conversational",
        "category": "conversational",
        "reps": {
            "raw_en": "Hey, I was wondering if you could help me figure out why my computer keeps running out of memory whenever I open too many browser tabs, and maybe suggest some things I could do about it?",
            "concise_en": "Why does my computer run out of memory with many browser tabs open, and what can I do about it?",
            "terse_en": "Many browser tabs -> out of memory. Why + fixes?",
            "raw_zh": "嘿，我想知道你能否帮我弄清楚为什么每当我打开太多浏览器标签页时，我的电脑总是内存不足，也许可以建议一些我可以采取的措施？",
            "concise_zh": "为什么打开很多浏览器标签页时电脑会内存不足？我该怎么办？",
            "foldlang_sym": "q: tabs>>N -> OOM; why+fixes",
            "foldlang_alias": "q: tabs>>N -> OOM; why+fixes",
        },
    },
    {
        "id": "long_technical",
        "category": "long_technical",
        "reps": {
            "raw_en": "We are designing a distributed job queue. Requirements are as follows: jobs must be processed exactly once even if workers crash mid-job, the queue must support delayed scheduling of jobs to run at a specific future time, priorities from 0 to 9 where 0 is the most urgent, horizontal scaling of workers without any coordination service such as ZooKeeper, at-least-once delivery is acceptable only for the dead-letter path, and the whole thing must run on PostgreSQL without any additional message broker. Please propose a schema and the locking strategy, and explain how visibility timeouts would be implemented.",
            "concise_en": "Designing a distributed job queue on PostgreSQL only (no broker, no ZooKeeper). Requirements: exactly-once processing despite worker crashes; delayed scheduling to a future time; priorities 0-9 (0 most urgent); horizontally scalable workers with no coordination service; at-least-once acceptable only on the dead-letter path. Propose schema + locking strategy; explain visibility-timeout implementation.",
            "terse_en": "Distributed job queue on Postgres only (no broker/ZooKeeper). Reqs: exactly-once despite crashes; delayed scheduling; priority 0-9 (0=urgent); scale workers w/o coordinator; at-least-once only for dead-letter. Give schema + locking + visibility-timeout impl.",
            "raw_zh": "我们正在设计一个分布式作业队列。要求如下：即使工作进程在作业中途崩溃，作业也必须恰好处理一次；队列必须支持将作业延迟调度到特定的未来时间运行；优先级从0到9，其中0最紧急；工作进程可水平扩展，无需ZooKeeper等协调服务；仅死信路径可接受至少一次投递；整个系统必须在PostgreSQL上运行，不使用额外的消息代理。请提出模式设计和锁定策略，并解释如何实现可见性超时。",
            "concise_zh": "仅用PostgreSQL设计分布式作业队列（无消息代理、无ZooKeeper）。要求：工作进程崩溃也要恰好一次处理；支持延迟到未来时间调度；优先级0-9（0最紧急）；工作进程可水平扩展且无协调服务；仅死信路径接受至少一次。给出表结构+锁策略，并解释可见性超时实现。",
            "foldlang_sym": "design job-queue db=pg-only !broker !zk req={exactly-once@crash, delay-sched, prio0-9(0=hi), hscale-nocoord, at-least-once=DLQ-only} out={schema, locking, visibility-timeout}",
            "foldlang_alias": "design job-queue pg-only !broker !zk: exactly-once@crash, delay-sched, prio0-9, hscale-nocoord, ALO=DLQ-only -> schema+locking+vis-timeout",
        },
    },
    {
        "id": "tool_call",
        "category": "tool_call",
        "reps": {
            "raw_en": "Please search the project files for every place where the function parse_config is called, and then return a complete list of the file paths together with the line numbers where each call occurs.",
            "concise_en": "Search project files for all calls of parse_config; return complete list of file paths with line numbers.",
            "terse_en": "Find all parse_config call sites; list path:line for each.",
            "raw_zh": "请在项目文件中搜索调用parse_config函数的每一个位置，然后返回完整的文件路径列表以及每次调用出现的行号。",
            "concise_zh": "在项目文件中搜索所有parse_config调用，返回完整的文件路径及行号列表。",
            "foldlang_sym": "grep parse_config scope=proj out=path:line all=1",
            "foldlang_alias": "K10 parse_config K11",
        },
    },
    {
        "id": "multi_agent",
        "category": "multi_agent",
        "reps": {
            "raw_en": "Agent B, you are receiving the results of the code analysis from Agent A. Your task is to take each finding, verify whether it is a real issue, discard false positives, and forward only the confirmed issues to Agent C together with a severity rating from 1 to 5 for each confirmed issue.",
            "concise_en": "Agent B: receive Agent A's code-analysis findings; verify each, discard false positives, forward only confirmed issues to Agent C with severity 1-5 each.",
            "terse_en": "B: verify A's findings, drop FPs, send confirmed to C w/ severity 1-5.",
            "raw_zh": "B代理，你正在接收来自A代理的代码分析结果。你的任务是对每个发现进行验证，确认它是否是真实问题，丢弃误报，只将确认的问题连同1到5的严重性评级转发给C代理。",
            "concise_zh": "B代理：接收A代理的代码分析结果；逐项验证，丢弃误报，仅将确认的问题连同1-5严重性评级转发给C代理。",
            "foldlang_sym": "B<-A.findings: verify each, drop FP, ->C confirmed+sev1-5",
            "foldlang_alias": "B<-A.findings: verify, drop FP, ->C +sev1-5",
        },
    },
    {
        "id": "repeated_boilerplate",
        "category": "repeated_instruction",
        "reps": {
            "raw_en": "As always, remember the standing rules for this project: do not break existing functionality, run the tests before finishing, preserve all public APIs, update the documentation whenever behavior changes, and never modify files that are unrelated to the current task.",
            "concise_en": "Standing project rules: don't break existing functionality; run tests before finishing; preserve public APIs; update docs on behavior change; never touch unrelated files.",
            "terse_en": "Rules: no breakage, run tests, keep APIs, sync docs, scope-only edits.",
            "raw_zh": "一如既往，请记住本项目的常规规则：不要破坏现有功能，完成前运行测试，保留所有公共API，行为变化时更新文档，绝不修改与当前任务无关的文件。",
            "concise_zh": "项目常规规则：不破坏现有功能；完成前运行测试；保留公共API；行为变化时更新文档；绝不改无关文件。",
            "foldlang_sym": "rules={!break,tests,api-keep,docs-sync,scope-only}",
            "foldlang_alias": "P1",
        },
    },
]

# The session dictionary that foldlang_alias representations assume.
# Bootstrap text = what must exist once in context for aliases to resolve.
BOOTSTRAP = (
    "DICT v1 (use these codes): "
    "K1=analyze the program; K2=preserve existing functionality; "
    "K3=do not break existing functionality; K4=run the full test suite after; "
    "K5=preserve all public APIs exactly; K6=update documentation to match; "
    "K7=do not modify unrelated files; K8=research state of the art; "
    "K9=structured report with citations; K10=search project files for; "
    "K11=return complete list of file paths with line numbers; "
    "P1=standing rules: K3+K4+K5+K6+K7."
)
