<!--
  ~ Copyright (c) 2025-2026 Datalayer, Inc.
  ~
  ~ BSD 3-Clause License
-->

[![Datalayer](https://assets.datalayer.tech/datalayer-25.svg)](https://datalayer.io)

[![Become a Sponsor](https://img.shields.io/static/v1?label=Become%20a%20Sponsor&message=%E2%9D%A4&logo=GitHub&style=flat&color=1ABC9C)](https://github.com/sponsors/datalayer)

# 🤖 👥 Agent Teams

[![PyPI - Version](https://img.shields.io/pypi/v/agent-teams)](https://pypi.org/project/agent-teams)
[![npm](https://img.shields.io/npm/v/%40datalayer%2Fagent-teams)](https://www.npmjs.com/package/@datalayer/agent-teams)

## The Datalayer orchestration extension

`agent_teams.a2a` (Python) and `@datalayer/agent-teams` (TypeScript)
implement the [Datalayer orchestration
extension](https://datalayer.ai/extensions/orchestration/v1) — an optional
A2A extension that lets a worker and an orchestrator say the things about
*delegated work* that A2A leaves unsaid: which execution a delegation is
part of and where it sits in its tree, a budget to decline before
exceeding, a checkpoint to resume from, and instructions delivered to a
turn already in progress.

```python
from agent_teams.a2a import ExecutionRef, build_delegation_meta

meta = build_delegation_meta(
    ExecutionRef(execution_id="exec_1", root_execution_id="exec_1", depth=0),
    budget={"outputTokens": 4000},
)
```

```typescript
import { buildDelegationMeta } from '@datalayer/agent-teams';

const meta = buildDelegationMeta(
  { executionId: 'exec_1', rootExecutionId: 'exec_1', depth: 0 },
  { budget: { outputTokens: 4000 } },
);
```

Nothing here requires the rest of this package, and nothing here imports
Datalayer's own platform — `agent_teams.a2a.orchestration_extension` and
`@datalayer/agent-teams`'s `src/orchestrationExtension.ts` are each a
standalone module (Python built on `fasta2a` alone; TypeScript with zero
runtime dependencies at all) implementing the identical wire shape, field
for field. A worker implementing none of it still runs; see the normative
specification and its JSON Schema at
[`docs/extension-v1.md`](https://github.com/datalayer-research/context-orchestration-protocols/blob/main/docs/extension-v1.md)
for the full field-by-field contract both modules implement.

This is distinct from `agent_teams.a2a.extensions`' own
**team-coordination** extension (`https://datalayer.io/ext/team-coordination/v1`)
— member assignment, task dependencies, health and reactions for a team's
*own* internal coordination. The two extensions are independent and
separately negotiated; a card may advertise either, both, or neither.
