# File-organization diagnosis

Use this reference only when the resolved task ID matches
`FileOrganization_[0-9]+_[0-9]+`.

## Evidence boundaries

- Treat the raw JSONL Trace as the only evidence of what XiaoYi attempted,
  failed, retried, or completed.
- Use metadata and deterministic Judge results only to establish requested final
  filesystem state and observed rubric failures.
- Do not infer device state from XiaoYi prose when no filesystem tool result or
  pulled output supports it.

## Investigation order

1. Find the root Agent terminal status and every filesystem or shell tool span.
2. Reconstruct the requested mutation: source paths, destination paths, file
   types, selection predicates, overwrite/delete scope, and required archive or
   rename behavior.
3. Check confirmation handling. Distinguish an unresolved permission/choice gate
   from a courtesy question after concrete completion.
4. Check command portability. HarmonyOS tools commonly execute through `/bin/sh`;
   flag Bash-only process substitution (`< <(...)`), arrays, `[[ ... ]]`, and
   unsupported GNU options such as `grep -P` only when the Trace shows the actual
   failure output or nonzero status.
5. Check mutation results and recovery. A later unrelated success does not recover
   a failed move/delete/rename; require a compatible retry and successful result
   for the same intended operation.
6. Check final verification. Directory listings or file checks must cover the
   exact relevant roots and prove both required presence and required absence.
7. Check output collection separately. A correct device mutation can still yield
   a failed evaluation when the latest clean Desktop/Download/Documents snapshot
   is incomplete or stale.

## Finding and change mapping

- Conversation gate or premature completion: target
  `xiaoyi-auto-continue/SKILL.md` or `run_test.py`.
- Incorrect shell construction or filesystem tool behavior: target
  `xiaoyi-auto-continue/SKILL.md` when the fix is a prompt/policy guardrail;
  target `task_executor.py` only when the Runner implementation actually owns
  the failing command path demonstrated by the Trace.
- Cleanup, setup, force-stop, log pull, or final snapshot lifecycle failure:
  target `run_test.py`, `task_executor.py`, or `setup_device.py` according to the
  responsible layer.
- Never propose edits to dataset metadata, rubrics, source fixtures, pulled
  outputs, Judge results, or unrestricted runner-core files.

Keep one finding per material root problem. When the same command syntax fails
repeatedly, report one finding with an occurrence count and the shortest complete
evidence chain showing command, status/error output, and recovery or impact.
