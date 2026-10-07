---
description: Build one FloodWatch feature end to end (scout -> builder -> refuter)
argument-hint: <feature description>
---

Feature request: $ARGUMENTS

1. Check `git status`. If there are uncommitted changes, stop and ask the user to commit a savepoint first.
2. Spawn the `scout` agent with the feature request. Wait for its brief.
3. Spawn the `builder` agent with the feature request plus the scout brief.
4. Spawn the `refuter` agent on the resulting diff (`git diff`).
5. If refuter returns BLOCK, send its findings back to `builder` and repeat step 4 (max 2 rounds). If still BLOCK, stop and report.
6. Report to the user: summary of the change, files touched, pytest/ruff results, refuter verdict. Do not commit; suggest the commit message.
