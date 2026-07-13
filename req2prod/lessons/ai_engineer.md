# Lessons — ai_engineer

## AI-01 — Open the real orchestration code
- **Source:** operating principle
- **Severity:** correctness
- **Trigger:** reviewing agent/LLM design.
- **Rule:** the app's core is a CrewAI multi-agent pipeline — agents/tasks declared in
  YAML, wired in Python, calling Anthropic models. Open the real agent/task/
  orchestration code, not just file names. Judge whether the design is documented and
  structured rather than ad hoc.

## AI-02 — Keys from env; guardrails; deliberate model choice
- **Source:** operating principle
- **Severity:** correctness
- **Trigger:** assessing AI engineering quality.
- **Rule:** check that API keys are loaded only from environment variables (never
  inline), that there's some evaluation or guardrail against bad model output, and
  that model choice per agent reflects real cost/latency/risk judgment — a cheaper
  model for low-stakes steps, a stronger one for anything pushing to production —
  rather than one model used everywhere without reasoning.
