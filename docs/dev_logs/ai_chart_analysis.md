# AI Chart Analysis

### [2026-10-16 14:34:48 UTC+8] - Modification Phase: Port chart AI review to the previous branch
- **Author**: shudgai999 / Copilot SDK
- **Target Files & Lines**: `core/services/symbol_runner.py:15, 80-141` (`_process_wild_swing_symbol`); `core/vision/chart_guard.py:69, 159, 324` (`DynamicVisionGatekeeper`, chart rendering, AI review); `requirements.txt:9-11`; `.env.example:339-346`; `tests/test_vision_chart_guard.py:29-196`; `tests/test_dynamic_vision_gate.py:15-119`.
- **Modification Description**: Added in-memory chart rendering and AI review before WILD_SWING orders, with local Qwen priority, Gemini fallback, configurable outage behavior, and per-symbol rejection cooldown. Removed the dependency on the newer branch's `core.swing` models so the module runs on this branch.
- **Trigger Reason & Requirement**: User requested returning to `refactor/clean-gates-and-peak-exit` and adding the AI chart-image analysis feature.
- **Verification & Test Status**: 41 targeted tests passed; compile and `git diff --check` passed; full dependency resolution dry-run passed. No commit, push, deployment, or service restart. Unfinished Swing work remains preserved in stash `83c90644`.
