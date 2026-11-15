# User-facing onboarding

Use at ResearchPilot activation, on a greeting/help request, or when no concrete
research request exists. Proactively orient the user in their language. Read
the current project, saved configuration and active-study index first; briefly
say what is already known and ask only for the missing business intent. A usage
question or startup alone is not permission to run examples or training.

The primary entry is the Codex client's `/` Skill picker: select ResearchPilot and
send the selected Skill with the default guide prompt or an ordinary task.
Selection alone does not send a turn. Natural-language invocation remains an
optional convenience; no CMD, separate CLI client or bootstrap is required.
Do not make users choose original versus improved SDM, supply Skill paths, or
repeat known platform/project settings. Resolve the approved version and route
internally. A concrete task should start read-only investigation and planning;
do not put a generic help/menu interview in front of an already stated goal.

The everyday user entry is ONE native `bmad-ml` Skill; SDM is its bundled research
implementation. Activate it in the current Codex conversation. Do not open a
separate CLI, invoke a CMD or require per-project registration. Infer the current
project and execution environment; ask for a different location only if needed.
Infer the baseline, data and commands from available project/configuration first.
Ask only material missing questions; do not make users author internal JSON,
signatures, lifecycle records or select every specialist Skill.

If the name is not discoverable, diagnose the installation/routing gap. An
absolute SKILL.md path is a temporary source-mode fallback, not the final
onboarding experience. Do not claim registration because source
files exist, or route to an old installed copy without comparing its provenance.
Install the complete native Skill folder, preserving existing configuration and
runs. The old full-module sdm-setup installer is for explicit legacy maintenance,
not a prerequisite for research onboarding.

Use these as agent intent routes; show copyable fallback examples only when useful:

1. New study: "这个分类模型效果不好，帮我分析和改进，目标 macro-F1 0.95。"
2. Continue: "继续当前研究，沿用原目标和授权。"
3. Status: "看当前研究进度，告诉我最优、最新结果和下一步。"

After a new initial plan, show a brief project/target/baseline/execution summary
and the actual first action. Reuse authorization already supplied; request only
what is truly missing. When the user authorizes continuous research, prepare the
runtime and actually start the applicable pipeline, rather than just giving
internal script instructions back to the user. Do not impose a trial cap.

At startup/handoff report the active runtime/configuration and the derived
component-pipeline/PROGRESS.md or progress.json location, the actual current task
identity when known, and whether continuation is autonomous or waiting for a
specific input. Status reports must read the active index/projection, not old
hand-written summaries. Explain polling freshness and distinguish a persisted
snapshot from a live host check.

Teach plain-language pause/resume/stop. Pausing the coordinator preserves already
submitted remote tasks and pauses coordinator observation; do not promise drain
or collection while paused. Stopping a platform task is a distinct explicit
request. Keep the host/network available for background work; do not promise an
OS-login service or desktop-chat milestone push that is not installed.

Keep technical schemas, historic node counts and maintenance details behind
task-specific references. Existing authorized work should continue normally;
do not force an onboarding interview every time the user says "继续".

## Guided first conversation

After inspection, use the user's language and one concrete next question, not a
catalog of scripts or an empty "what do you want?". For example:
"我已找到当前项目。我们先明确研究任务：你希望改进什么效果？可以直接给我代码目录、已有结果或一段问题描述；数据和基线我会先帮你查。"
If a study exists, state its goal, best/latest result and actual phase first.
Ask whether to inspect it or define a different task only when intent is absent;
never resume a paused study on startup alone.

Collect missing information progressively, usually one or two related questions:
- Task/data/current baseline: infer from code and files; if missing, accept a path,
  result report or ordinary description. Do not make users fill all three first.
- Success: propose an appropriate primary metric and ask for the business target
  if unknown. Ask which classes/scenarios must not regress when relevant. Do not
  invent a threshold for them or turn every secondary metric into a stop gate.
- Evaluation and execution: summarize known data/split/checkpoint definitions,
  supported producer/metrics, platform and resource scope. Explain an unsupported
  evaluator before dispatch. Reuse existing authorization; obtain only missing
  initial plan/execution agreement.

Then show a short "已知 / 待确认 / 下一步" summary and perform the next authorized
action. Users do not need to know SDM internals, node counts, runtime schemas or
how to launch a dashboard. After study registration, offer/open the local view
using the existing dashboard guide; activation alone creates no background chat,
training or duplicate Web project. Foreground discussion stays in this Codex chat;
the study index and durable task/result records bind the Web to the research.
