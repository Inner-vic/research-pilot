---
name: bmad-ml
description: 在当前项目启动 ResearchPilot 引导、分析实验结果或持续研究。首次使用自动准备本机运行环境与工作台。
---

# ResearchPilot source entry

The repository root is three levels above this Skill directory, containing README.md and skills/. Resolve it from this file. Read its
`skills/bmad-ml/SKILL.md`; it is the canonical entry, not another experiment engine.
If the current workspace has no `.bmad-ml/ENVIRONMENT.json`, run the repository's
`scripts/setup.py --project-root CURRENT_WORKSPACE --no-install` with available
Python 3.11+. This prepares an isolated official SDK and local workbench, without
model/platform calls, global authentication changes or training. On network or
Python failure report the exact prerequisite; do not fake readiness or repeat an
unknown installation. Reuse the saved environment thereafter. Start/reuse the
workbench with `scripts/ensure-dashboard.py` when its ready health check is stale.
Continue guided onboarding in THIS Codex conversation. Existing studies keep
original identities and pause/authorization state. LPAI is configured separately.
