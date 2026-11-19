---
name: analyst
description: "Answer a business question about Beam's data end to end: frame, profile, analyse, validate, deliver."
user-invocable: true
argument-hint: "<the business question>"
---

Answer this question about Beam's data end to end: $ARGUMENTS

If the question above is empty, ask for the question and stop. Never invent one.

## The method

Every step below follows these rules.

1. **Frame the decision before touching data.** If the question does not say what decision it informs and who decides, ask; never reconstruct it.
2. **Never answer a table question from the schema alone.** Profile first; a table without a YAML in `knowledge/beam/` is profiled before it is used, and Profile proposes the YAML.
3. **A naked number is not a finding.** Every number carries a comparison (prior period, segment or benchmark) or says none is available; percentage points and relative percent are named.
4. **Cite the table, the filter and the code.** Every number in a brief traces to `analysis.py` and a result table in the run folder.
5. **Parts sum to totals and joins do not fan out.** The checks in `helpers/checks.py` run in code, not in prose.
6. **Say what was not checked.** Open periods, unmatured trials and untested assumptions are part of the answer.
7. **Read the knowledge folder before writing code, and propose what you learn.** Read `knowledge/beam/` (the table YAMLs, `quirks.md`, `corrections.md`) first; when the user corrects you, append the correction to `corrections.md` as one dated line and continue.
8. **Propose changes to the YAML.** When any step finds something that requires updating, correcting, clarifying or adding to a table's YAML in `knowledge/beam/`, it proposes the change to the user and does not edit the YAML. Each proposal gives the file (`knowledge/beam/<table>.yaml`), the field or section, the proposed change and the reason. Collect the proposals from every step and show them once, numbered, at the end of the run. Apply none without the user's approval.

## The steps

1. Run the `frame` skill with the question above: read `.claude/skills/frame/SKILL.md` and follow it. Note the run folder it created.
2. For each table named under "Tables likely involved" in the frame note that has no `knowledge/beam/<table>.yaml`, run the `profile` skill on it (`.claude/skills/profile/SKILL.md`).
3. Run the `analyse` skill on the run folder (`.claude/skills/analyse/SKILL.md`).
4. Run the `validate` skill on the run folder (`.claude/skills/validate/SKILL.md`). If its verdict is **Fix first**, fix `analysis.py` according to the findings, rerun it, and run `validate` again. At most two rounds. If it is still **Fix first**, stop and report the findings; do not write a brief. If its verdict is **Think on it**, apply any finding that is a small change to `analysis.py` (a filter, a baseline, a label) and rerun the script once, and carry every remaining finding into the brief's Checks section as not verified; do not run `validate` again.
5. Run the `deliver` skill on the run folder (`.claude/skills/deliver/SKILL.md`).
6. Finish with the path of the run folder, the verdict from `review.md`, and the Key Takeaways from `brief.md` copied verbatim, then the numbered YAML change proposals from the steps, if any. Nothing else on the CLI.
