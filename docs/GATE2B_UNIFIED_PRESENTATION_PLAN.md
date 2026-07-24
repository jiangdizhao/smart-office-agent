# Gate 2B unified presentation plan

Gate 2B no longer asks GPT Realtime to choose between overlapping single-action tools and a separate compound-sequence tool.

GPT Realtime is exposed exactly one planning function:

```text
presentation_plan
```

Its output contains one to eight ordered steps selected from the bounded presentation capability set.

Examples:

```json
{
  "steps": [
    {"name": "presentation_next_slide"}
  ]
}
```

```json
{
  "steps": [
    {"name": "presentation_open_configured"},
    {"name": "presentation_start_slideshow"},
    {"name": "presentation_go_to_slide", "slide_number": 5}
  ]
}
```

Responsibility is separated as follows:

- GPT Realtime interprets the user's Chinese or English utterance and produces the ordered semantic plan.
- Backend validates the plan schema, action names, argument fields, step count, and slide numbers.
- A validated one-step plan executes directly through the Gate 1 controller and verifier.
- A validated multi-step plan enters the existing TaskSession and TaskGraph runtime and is verified step by step.
- A failed step prevents later steps from running.
- Visitor permission denial occurs before plan execution.

No keyword counter, conjunction parser, regular-expression command splitter, or second backend LLM decides whether an utterance is single-step or compound. The distinction is derived only from the number of validated structured steps produced by GPT Realtime.

The legacy direct tool-name path remains Backend-only for compatibility with older clients. It is not exposed in the current GPT Realtime tool schema.
