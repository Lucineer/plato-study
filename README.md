# PLATO Study — Expert Research Room

Spawn expert agents on git branches to do research while you work on other things. Come back to rooms full of findings. Rewind if they drift, fork from the last good point.

## Core Concept
An expert is a subagent working on a branch. Their "headspace" IS the branch — every file they create, every insight they write, is a commit. You can:
- **Listen in** — watch their commits stream in real-time
- **Course-correct** — drop a command to redirect them mid-work
- **Checkpoint** — bookmark when they're on track
- **Rewind** — reset to a checkpoint if they drift off
- **Fork** — start a fresh attempt from a good checkpoint, refund their budget

## Actions
- `spawn` — create expert (name, topic, brief, model, budget_tokens, max_rounds)
- `journal` — expert posts research notes (type: note/finding/question/redirect/dead-end)
- `checkpoint` — bookmark current state with label
- `rewind` — reset expert branch to a checkpoint sha
- `fork` — create new expert from checkpoint, inherit config, fresh budget
- `status` — overview of all experts, their progress, branches

## Expert Budget
Each expert gets a model + token budget + max rounds. They work autonomously until:
- Budget exhausted
- Max rounds reached
- You checkpoint/rewind/fork them

## Journal Entry Types
- `note` — general thinking
- `finding` — confirmed discovery
- `question` — something to investigate
- `redirect` — changing direction based on new info
- `dead-end` — this path didn't work out

## Rewind/Fork Flow
1. Expert works for 10 rounds, great progress
2. Round 11-14 they drift
3. You `checkpoint` round 10 (or it was auto-checkpointed)
4. `rewind` to that checkpoint
5. `fork` into a new expert with refined brief
6. New expert starts from the good state with fresh budget
