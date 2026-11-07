"""Usage:  python -m orchestrator.cli run slugify|shortener|brownfield|ambiguous [--approve ask|auto|deny:<node>] [--fail developer:2]
                                              [--leak-secret] [--revise-design ok|fail|reject]
                                              [--backoff 0.1] [--max-replans 3] [--run-root runs]
                                              [--violate secret|import|undocumented|thin-tests|empty-docs]

Policy demos (each makes named policies fire; names are per workflow, see docs/policies.md):
  slugify / shortener:  --violate secret | import | undocumented | thin-tests | empty-docs
  shortener only:       --violate ui-innerhtml | ui-cdn | ui-unlabelled
  brownfield:           --violate scope-creep | api-break | ungrounded-impact | no-regression-test | drop-tests | changelog-no-ticket
  shortener, ambiguous: --violate plan-cycle | plan-misses-criterion | plan-wrong-order
  ambiguous:            --violate ignores-decision | scope-creep | untraced-tests | changelog-no-ticket

Ambiguous scenario ("make links expire sensibly"): humans answer the questions, and can change an answer later
  --answer default_ttl_days=90 --answer max_ttl_days=365 --answer ttl_applies_to_aliases=no     (no --answer: high-risk blocks)
  --revise-answer default_ttl_days=180      automatic re-plan: only the work that depended on that answer is redone
  --revise-answer default_ttl_days=<same>   identical answer -> nothing happens (no churn)
  --revise-answer ... --break-on-revise     the re-run fails -> the whole change, including the answer, is rolled back
  --revise-design ok --max-replans 0   change control refuses the re-plan; system stays in last good state

Resilience demos:
  --fail developer:2                retries recover the node
  --fail developer:5                retries exhausted -> FALLBACK agent recovers it
  --revise-design ok                design changes after release -> re-plan, downstream redone
  --revise-design fail              ...but the re-run breaks -> whole change ROLLED BACK
  --revise-design reject            ...and the human rejects the revised design -> ROLLED BACK
"""
from __future__ import annotations

import argparse
import sys

from .governance import approvals
from .governance.policies import GovernanceError
from .runner import run_workflow
import inspect

from .workflows import ambiguous, brownfield, shortener, slugify

WORKFLOWS = {"slugify": slugify, "shortener": shortener, "brownfield": brownfield, "ambiguous": ambiguous}


def scripted_answers(wf, names) -> dict | None:
    """Turn --violate names into per-role scripted LLM answers.

    A normal violation is the agent's FIRST answer (then it recovers); a persistent one repeats, so the
    policy must hold and the run halts.
    """
    scripted: dict[str, list[str]] = {}
    recovering: list[str] = []
    for name in names:
        role, bad, persistent = wf.VIOLATIONS[name]
        if persistent:
            scripted[role] = [bad]
        else:
            scripted.setdefault(role, []).append(bad)
            recovering.append(role)
    good = wf.make_llm().defaults
    for role in dict.fromkeys(recovering):
        scripted[role].append(good[role])              # then a good answer
    return scripted or None


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):            # Windows consoles are not always UTF-8
        sys.stdout.reconfigure(errors="replace")
    p = argparse.ArgumentParser(prog="orchestrate")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("workflow", choices=sorted(WORKFLOWS))
    r.add_argument("--approve", default="ask", help="ask | auto | deny:<node_id>")
    r.add_argument("--fail", action="append", default=[], help="inject LLM failures, e.g. developer:2")
    r.add_argument("--leak-secret", action="store_true", help="developer's first answer contains a hardcoded secret")
    r.add_argument("--revise-design", choices=["ok", "fail", "reject"],
                   help="after a successful run, revise the design (dynamic re-plan)")
    r.add_argument("--backoff", type=float, default=0.0, help="base retry backoff in seconds (doubles each retry)")
    r.add_argument("--violate", action="append", default=[], metavar="NAME",
                   help="make the mock LLM produce output that breaks one named policy (names depend on the workflow)")
    r.add_argument("--answer", action="append", default=[], metavar="ID=VALUE",
                   help="human answer to a clarifying question (workflows that ask questions)")
    r.add_argument("--revise-answer", action="append", default=[], metavar="ID=VALUE",
                   help="after a successful run, change an earlier answer (automatic re-plan)")
    r.add_argument("--break-on-revise", action="store_true", help="make the developer fail during the re-run after --revise-answer")
    r.add_argument("--max-replans", type=int, default=3, help="change control: re-plans allowed per run")
    r.add_argument("--run-root", default="runs")
    a = p.parse_args(argv)

    fail = {k: int(v) for k, v in (x.split(":") for x in a.fail)}
    violations = list(a.violate) + (["secret"] if a.leak_secret else [])
    wf = WORKFLOWS[a.workflow]
    unknown = [v for v in violations if v not in wf.VIOLATIONS]
    if unknown:
        p.error(f"--violate {unknown[0]!r} is not defined for '{a.workflow}'; choose from: {', '.join(sorted(wf.VIOLATIONS))}")
    scripted = scripted_answers(wf, violations)
    llm = wf.make_llm(fail_times=fail, scripted=scripted)
    approver = (approvals.cli_approver if a.approve == "ask" else approvals.auto_approver if a.approve == "auto"
                else approvals.deny_approver(*a.approve.split(":")[1:]))

    revisions = ()
    if a.revise_design:
        if a.revise_design == "reject":      # approve the first design, reject the revised one
            seen = []
            inner = approver
            def approver(node, output, _inner=inner, _seen=seen):
                if node.id == "design":
                    _seen.append(1)
                    return len(_seen) == 1
                return _inner(node, output)

        def break_developers():
            if a.revise_design == "fail":
                llm.inject_failures("developer", 99)
                llm.inject_failures("developer_fallback", 99)
        revisions = (("design", "design revised after review", break_developers),)

    def pairs(items):
        try:
            return {k: v for k, v in (x.split("=", 1) for x in items)}
        except ValueError:
            p.error("expected ID=VALUE")

    extra = {}
    if "asker" in inspect.signature(wf.build).parameters:       # workflows that ask clarifying questions
        extra["asker"] = approvals.layered_asker(approvals.dict_asker(pairs(a.answer)),
                                                 approvals.cli_asker if a.approve == "ask" else approvals.no_asker)
    elif a.answer or a.revise_answer:
        p.error(f"'{a.workflow}' does not ask questions, so --answer/--revise-answer do not apply")
    edits = ()
    if a.revise_answer:
        edits = tuple(("clarifications", wf.answer_edit(k, next((o for o in [v] if o), v)), f"human changed {k} to {v}")
                      for k, v in pairs(a.revise_answer).items())
    def break_developers():
        if a.break_on_revise:
            llm.inject_failures("developer", 99)
            llm.inject_failures("developer_fallback", 99)

    graph, agents = wf.build(llm, **extra)
    try:
        res = run_workflow(graph, agents, approver, run_root=a.run_root, revisions=revisions, edits=edits,
                           on_replan=break_developers, assets=getattr(wf, "ASSETS", None),
                           backoff_base_s=a.backoff, max_replans=a.max_replans)
    except GovernanceError as exc:
        print(f"\nWORKFLOW REJECTED BY GOVERNANCE LINT:\n  " + "\n  ".join(str(v) for v in exc.violations))
        return 2
    print(f"\nStatus: {res['status'].value} | run dir: {res['run_dir']}")
    print((res["run_dir"] / "report.md").read_text(encoding="utf-8"))
    for rev in res["revisions"]:
        print("REVISION:", f"redid {len(rev.affected)} node(s): {', '.join(sorted(rev.affected))}" if rev.changed
              else (rev.why if rev.accepted else f"REJECTED - {rev.why}"))
    if any(not rev.accepted for rev in res["revisions"]):
        return 3                       # a requested change was refused; the system stays in its last good state
    return 0 if res["metrics"]["run_success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
