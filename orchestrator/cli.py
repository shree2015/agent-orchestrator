"""Usage:  python -m orchestrator.cli run slugify|shortener [--approve ask|auto|deny:<node>] [--fail developer:2]
                                              [--leak-secret] [--revise-design ok|fail|reject]
                                              [--backoff 0.1] [--max-replans 3] [--run-root runs]
                                              [--violate secret|import|undocumented|thin-tests|empty-docs]

Policy demos (each makes ONE named policy fire; see docs/policies.md):
  --violate secret        security/no-hardcoded-secrets      -> developer retried
  --violate import        security/dependency-allowlist      -> developer retried
  --violate undocumented  compliance/public-api-documented   -> developer retried
  --violate thin-tests    compliance/minimum-tests           -> run halts (tests keep failing policy)
  --violate empty-docs    compliance/docs-cover-public-api   -> run halts (docs keep failing policy)
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

from .governance import approvals
from .governance.policies import GovernanceError
from .runner import run_workflow
from .workflows import shortener, slugify

WORKFLOWS = {"slugify": slugify, "shortener": shortener}


def main(argv=None) -> int:
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
    r.add_argument("--violate", action="append", default=[],
                   choices=["secret", "import", "undocumented", "thin-tests", "empty-docs"],
                   help="make the mock LLM produce output that breaks one named policy")
    r.add_argument("--max-replans", type=int, default=3, help="change control: re-plans allowed per run")
    r.add_argument("--run-root", default="runs")
    a = p.parse_args(argv)

    fail = {k: int(v) for k, v in (x.split(":") for x in a.fail)}
    violations = list(a.violate) + (["secret"] if a.leak_secret else [])
    wf = WORKFLOWS[a.workflow]
    fx = wf.FIXTURES
    bad_code = {"secret": fx["bad_secret"], "import": fx["bad_import"], "undocumented": fx["undocumented"]}
    scripted = {}
    dev_bad = [bad_code[v] for v in violations if v in bad_code]
    if dev_bad:                                   # bad answers first, then a good one
        scripted["developer"] = dev_bad + [fx["good_code"]]
    if "thin-tests" in violations:
        scripted["tester"] = [fx["thin_tests"]]   # persistent: the policy must hold
    if "empty-docs" in violations:
        scripted["doc_writer"] = [""]
    scripted = scripted or None
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

    graph, agents = wf.build(llm)
    try:
        res = run_workflow(graph, agents, approver, run_root=a.run_root, revisions=revisions,
                           backoff_base_s=a.backoff, max_replans=a.max_replans)
    except GovernanceError as exc:
        print(f"\nWORKFLOW REJECTED BY GOVERNANCE LINT:\n  " + "\n  ".join(str(v) for v in exc.violations))
        return 2
    print(f"\nStatus: {res['status'].value} | run dir: {res['run_dir']}")
    print((res["run_dir"] / "report.md").read_text())
    return 0 if res["metrics"]["run_success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
