"""Usage:  python -m orchestrator.cli run slugify|shortener|brownfield|ambiguous [--approve ask|auto|deny:<node>] [--fail developer:2]
                                              [--leak-secret] [--revise-design ok|fail|reject]
                                              [--backoff 0.1] [--max-replans 3] [--run-root runs]
         [--requirement TEXT | --requirement-file PATH] [--llm mock|anthropic] [--llm-roles a,b] [--model NAME] [--record DIR]
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
from .llm import anthropic_client
from .llm.base import LLMError
from .llm.routing import RecordingLLM, RoutingLLM
from .runner import run_workflow
import inspect
import os
from pathlib import Path

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
    r.add_argument("--requirement", metavar="TEXT", help="the requirement, in plain English (replaces the built-in one)")
    r.add_argument("--requirement-file", metavar="PATH", help="read the requirement from a UTF-8 text/markdown file")
    r.add_argument("--llm", choices=["mock", "anthropic"], default="mock",
                   help="mock = offline stored artifacts (default); anthropic = a real model (needs ANTHROPIC_API_KEY)")
    r.add_argument("--llm-roles", metavar="ROLES", help="hybrid: only these comma-separated roles use the real model "
                   "(e.g. requirements,architect,doc_writer); the rest stay on the mock")
    r.add_argument("--model", default=os.environ.get("ANTHROPIC_MODEL", anthropic_client.DEFAULT_MODEL),
                   help="model name for --llm anthropic (default: env ANTHROPIC_MODEL or %(default)s)")
    r.add_argument("--max-tokens", type=int, default=16000, help="per-call output limit (a cut-off answer is rejected)")
    r.add_argument("--max-llm-calls", type=int, default=40, help="hard budget on real model calls for this run")
    r.add_argument("--llm-timeout", type=float, default=300.0, help="seconds to wait for one model call")
    r.add_argument("--record", metavar="DIR", help="save the system prompt, prompt and response of every LLM call as JSON "
                   "(with --llm mock this is a free preview of exactly what a real run would send)")
    r.add_argument("--max-replans", type=int, default=3, help="change control: re-plans allowed per run")
    r.add_argument("--max-reworks", type=int, default=2, metavar="N",
                   help="change control: how many times a reviewer's rejection may send work back to be redone (default 2; 0 = halt at once)")
    r.add_argument("--max-lines-changed", type=int, metavar="N", help="change control: the most lines the code stage may produce "
                   "(shortener default 900, slugify 200); recorded in the audit log")
    r.add_argument("--max-ui-lines", type=int, metavar="N", help="change control: the most lines the web UI stage may produce "
                   "(shortener default 700); recorded in the audit log")
    r.add_argument("--run-root", default="runs")
    a = p.parse_args(argv)

    fail = {k: int(v) for k, v in (x.split(":") for x in a.fail)}
    violations = list(a.violate) + (["secret"] if a.leak_secret else [])
    wf = WORKFLOWS[a.workflow]
    unknown = [v for v in violations if v not in wf.VIOLATIONS]
    if unknown:
        p.error(f"--violate {unknown[0]!r} is not defined for '{a.workflow}'; choose from: {', '.join(sorted(wf.VIOLATIONS))}")
    real = a.llm == "anthropic"
    if real and (violations or fail):
        p.error("--violate/--leak-secret/--fail script the MOCK model; they cannot be combined with --llm anthropic")
    if a.llm_roles and not real:
        p.error("--llm-roles needs --llm anthropic")
    requirement = a.requirement
    if a.requirement_file:
        try:
            requirement = Path(a.requirement_file).read_text(encoding="utf-8")
        except OSError as exc:
            p.error(f"cannot read --requirement-file: {exc}")
    scripted = scripted_answers(wf, violations)
    llm = wf.make_llm(fail_times=fail, scripted=scripted)
    client = None
    roles = None
    if real:
        try:
            client = anthropic_client.AnthropicClient(model=a.model, max_tokens=a.max_tokens, max_calls=a.max_llm_calls,
                                                      timeout=a.llm_timeout)
        except LLMError as exc:
            print(f"ERROR: {exc}")
            return 2
        roles = [r.strip() for r in a.llm_roles.split(",") if r.strip()] if a.llm_roles else None
        llm = client if not roles else RoutingLLM(llm, {r: client for r in roles})
        print(f"REAL MODEL: {a.model} for {', '.join(roles) if roles else 'all agent roles'} | call budget {a.max_llm_calls} | "
              "your requirement text and the generated code are sent to the API")
    elif requirement:
        print("NOTE: the mock agents return stored artifacts and ignore the requirement text. Use --llm anthropic "
              "to see it drive the output.")
    if a.record:
        llm = RecordingLLM(llm, a.record)
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

    extra = {"requirement": requirement} if requirement else {}
    supported = getattr(wf, "SIZE_LIMITS", {})                      # the size limits this workflow lets the operator set
    for value, key, flag in ((a.max_lines_changed, "max_lines", "--max-lines-changed"), (a.max_ui_lines, "max_ui_lines", "--max-ui-lines")):
        if value is None:
            continue
        if key not in supported:
            p.error(f"{flag} does not apply to '{a.workflow}'" + (" (its limits are fixed by the scenario)" if not supported else f" (it accepts: {', '.join(supported)})"))
        if not 50 <= value <= 5000:
            p.error(f"{flag} must be between 50 and 5000")
        extra[key] = value
    limits = {k: extra.get(k, default) for k, default in supported.items()}
    if limits:
        print("Size limits (change control): " + ", ".join(f"{k.replace('_', ' ')} = {v}" for k, v in limits.items())
              + ("  [raised above the default; recorded in the audit log]" if any(v > supported[k] for k, v in limits.items()) else ""))
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
                           on_replan=break_developers, assets=getattr(wf, "ASSETS", None), llm=llm,
                           backoff_base_s=a.backoff, max_replans=a.max_replans, max_reworks=a.max_reworks,
                           run_config={"workflow": a.workflow, "llm": a.llm, "model": a.model if real else None, "roles": roles, "limits": limits,
                                       "approve": a.approve.split(":")[0], "max_replans": a.max_replans, "max_reworks": a.max_reworks})
    except GovernanceError as exc:
        print(f"\nWORKFLOW REJECTED BY GOVERNANCE LINT:\n  " + "\n  ".join(str(v) for v in exc.violations))
        return 2
    print(f"\nStatus: {res['status'].value} | run dir: {res['run_dir']}")
    if real:
        u = llm.usage
        print(f"LLM usage: {u['calls']} call(s), {u['input_tokens']} input tokens, {u['output_tokens']} output tokens")
    print((res["run_dir"] / "report.md").read_text(encoding="utf-8"))
    for rev in res["revisions"]:
        print("REVISION:", f"redid {len(rev.affected)} node(s): {', '.join(sorted(rev.affected))}" if rev.changed
              else (rev.why if rev.accepted else f"REJECTED - {rev.why}"))
    if any(not rev.accepted for rev in res["revisions"]):
        return 3                       # a requested change was refused; the system stays in its last good state
    return 0 if res["metrics"]["run_success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
