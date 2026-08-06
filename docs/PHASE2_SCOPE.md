# Phase 2 Scope — Unified Router + Reception Knowledge MVP

Phase 2 starts after the validated Phase 1 voice foundation has been merged into `main`.

## Goals

1. Replace the Phase 1 echo-style `/agent/turn` handler with a deterministic unified router.
2. Support these routes:
   - `realtime_direct`
   - `reception_knowledge`
   - `office_direct`
   - `office_planned_task`
   - `approval_action`
   - `clarification`
3. Add a minimal approved company knowledge source that runs locally without Dify or an external vector database.
4. Add visitor / employee / operator permission gates.
5. Keep Office execution disabled through `/agent/turn` until Phase 3; planned Office requests may create plan-only Task Sessions.
6. Preserve every Phase 1 voice and Milestone 2 task contract.
7. Provide a browser content page that can be opened on a secondary display for approved reception content.

## Safety boundaries

- Company facts must come from approved repository content.
- Visitor requests cannot create or execute Office tasks.
- `office_direct` is classification-only in Phase 2; it never claims that an application action succeeded.
- `office_planned_task` creates a plan-only Task Session (`execute=false`).
- Approval actions require an active task id and are forwarded to the existing task runtime only when valid.
- No Teams, PowerPoint, email, or Windows automation is added in this phase.

## Initial approved knowledge

The repository contains a conservative Smart Office demonstration profile only. It must be replaced or extended with organization-approved facts before a customer deployment. Each answer exposes a source identifier, content version, and update date.

## Acceptance

- Deterministic route coverage for reception, Office direct, Office planned, approval, greeting, stop, repeat, and unclear input.
- Visitor permission denial for Office actions.
- Employee plan-only Task Session creation for complex Office goals.
- Reception answers include source identifiers and a browser content URL.
- Existing `/agent/tasks` and Phase 1 Realtime endpoints continue to pass regression tests.
- Frontend production build passes.
