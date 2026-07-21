# Smart Office Virtual Host — Migration Baseline

Date: 2026-07-21

## Repository roles

- Product repository: `jiangdizhao/smart-office-agent`
- Integration branch: `integration/virtual-host`
- Voice technology donor: `jiangdizhao/emergency-wood-floor-greeter-demo`, branch `office`

The Smart Office product remains in `smart-office-agent`. The donor repository is read-only migration input; its wood-floor products, CRM, promotions, customer identity, face memory, sales policy, proactive narration, and retail UI must not be copied into this repository.

## Frozen starting points

- `smart-office-agent/main`: `f10ea5764932e2a667f11f1c1251f94636eac3ca`
- `emergency-wood-floor-greeter-demo/office` validated voice reference: `2b601df1b73cb630a88c785dd6537eca58b33820`
- At baseline capture, `office` contained one later `.gitignore`-only commit after the validated voice reference. Migration behavior is therefore pinned to the validated voice files at the `office` branch state inspected on 2026-07-21, not to retail-domain code.

## Existing Smart Office contracts to preserve

- `POST /agent/tasks`
- `GET /agent/tasks/{task_id}`
- `GET /agent/tasks/{task_id}/events`
- `POST /agent/tasks/{task_id}/approval`
- `POST /agent/tasks/{task_id}/cancel`
- Task Session, Task Graph, Executor, Verifier, Approval Gate, SSE events, cancellation, and JSONL logging
- Existing React task/debug console

## Phase 1 scope

Phase 1 introduces only the reusable voice foundation:

1. Generic Realtime WebRTC session proxy.
2. Persistent browser Realtime session.
3. Push-to-talk microphone attachment and release.
4. Realtime or Browser speech-recognition selection in the debug UI.
5. Explicit single voice-output ownership.
6. Long-audio lifecycle protection integrated into the runtime.
7. Minimal `POST /agent/turn` text contract.
8. Regression checks proving that the Milestone 2 task APIs still work.

## Explicit non-goals for Phase 1

- No Teams control.
- No PowerPoint control migration.
- No reception RAG.
- No visitor/employee authorization implementation.
- No 3D avatar.
- No wake word.
- No hidden voice-provider fallback.
- No automatic narration on user silence.
- No migration of wood-floor business state or UI.

## Safety and validation rules

- The OpenAI API key remains server-side and must never be committed.
- A failed selected voice provider reports an error and preserves text; it must not start a second speaker.
- The physical microphone track must be detached and stopped after every completed or aborted capture.
- A Realtime answer is complete only after the response completion event and, for audio, the output-audio stopped event.
- Browser, microphone, and real audio validation must be performed on the Windows demo machine before claiming end-to-end success.
