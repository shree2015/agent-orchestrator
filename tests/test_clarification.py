"""Clarification policies: who may answer, what may be answered, and whether the code reflects the answers."""
import json

from orchestrator.governance import gates as g
from orchestrator.governance.policies import clarification as cl
from orchestrator.workflows import ambiguous as am

REQS = am.DEFAULTS["requirements"]
CODE = am.render_code({"default_ttl_days": 90, "max_ttl_days": 365, "ttl_applies_to_aliases": "no"})


def decisions(**overrides):
    base = {"default_ttl_days": (90, "human"), "max_ttl_days": (365, "assumed"), "ttl_applies_to_aliases": ("no", "assumed")}
    base.update(overrides)
    out = [{"id": k, "answer": a, "decided_by": by, "rationale": "because" if by != "unresolved" else ""} for k, (a, by) in base.items()]
    return json.dumps({"decisions": out})


def run(policy, clar, **extra):
    return policy.evaluate({"requirements": REQS, "clarifications": clar, **extra})


def test_complete_valid_decisions_pass_the_whole_clarification_set():
    pset = cl.clarification_set(am.RULES)
    assert pset.evaluate({"requirements": REQS, "clarifications": decisions()}) == []


def test_every_question_needs_a_decision_and_no_decision_may_be_invented():
    partial = json.dumps({"decisions": [{"id": "default_ttl_days", "answer": 90, "decided_by": "human"}]})
    out = run(cl.decisions_cover_ambiguities(), partial)
    assert {"max_ttl_days", "ttl_applies_to_aliases"} <= {v.message.split("'")[1] for v in out}
    invented = json.loads(decisions())
    invented["decisions"].append({"id": "something_else", "answer": 1, "decided_by": "assumed"})
    assert any("never asked" in v.message for v in run(cl.decisions_cover_ambiguities(), json.dumps(invented)))


def test_high_risk_questions_need_a_human_and_an_assumption_is_not_enough():
    assert run(cl.high_risk_needs_human(), decisions()) == []
    assumed = run(cl.high_risk_needs_human(), decisions(default_ttl_days=(90, "assumed")))
    assert len(assumed) == 1 and "default_ttl_days" in assumed[0].message and "high-risk" in assumed[0].message
    unresolved = run(cl.high_risk_needs_human(), decisions(default_ttl_days=(None, "unresolved")))
    assert len(unresolved) == 1


def test_a_human_label_without_an_answer_does_not_count():
    assert run(cl.high_risk_needs_human(), decisions(default_ttl_days=(None, "human")))


def test_medium_and_low_risk_questions_may_be_assumed():
    assert run(cl.high_risk_needs_human(), decisions(max_ttl_days=(365, "assumed"), ttl_applies_to_aliases=("no", "assumed"))) == []


def test_answers_must_be_one_of_the_supported_options():
    out = run(cl.answers_within_options(), decisions(default_ttl_days=(45, "human")))
    assert len(out) == 1 and "45" in out[0].message and "[30, 90, 180]" in out[0].message
    assert run(cl.answers_within_options(), decisions(default_ttl_days=("90", "human")))     # text is not the option 90


def test_an_assumption_must_say_why():
    no_reason = json.loads(decisions())
    for d in no_reason["decisions"]:
        d["rationale"] = ""
    out = run(cl.assumptions_have_rationale(), json.dumps(no_reason))
    assert {v.message.split("'")[1] for v in out} == {"max_ttl_days", "ttl_applies_to_aliases"}


def test_answers_must_not_contradict_each_other():
    out = run(cl.decisions_consistent(am.RULES), decisions(default_ttl_days=(180, "human"), max_ttl_days=(90, "human")))
    assert len(out) == 1 and "180 days" in out[0].message and "90 days" in out[0].message
    assert run(cl.decisions_consistent(am.RULES), decisions(default_ttl_days=(90, "human"), max_ttl_days=(90, "human"))) == []


def test_a_rule_that_cannot_be_evaluated_is_a_violation_not_a_crash():
    out = run(cl.decisions_consistent(am.RULES), decisions(default_ttl_days=("180", "human"), max_ttl_days=(90, "human")))
    assert len(out) == 1 and "could not be compared" in out[0].message


def test_rules_wait_until_all_the_answers_they_need_exist():
    assert run(cl.decisions_consistent(am.RULES), decisions(default_ttl_days=(None, "unresolved"), max_ttl_days=(90, "human"))) == []


def test_the_set_fails_closed_without_questions_or_decisions():
    pset = cl.clarification_set(am.RULES)
    assert pset.evaluate({"requirements": REQS})                        # no decisions at all
    assert pset.evaluate({"clarifications": decisions()})               # no questions at all
    assert pset.evaluate({"requirements": "not json", "clarifications": "{}"})
    assert len(pset.evaluate({})) == 1                                  # reported once, not once per policy


def test_the_code_check_reports_missing_inputs_itself():
    assert cl.implementation_honours_decisions(am.HONOURED).evaluate({"requirements": REQS})
    assert cl.implementation_honours_decisions(am.HONOURED).evaluate(
        {"requirements": REQS, "clarifications": decisions()})          # no implementation artifact


# ---- decisions must reach the code -------------------------------------------------
def honours(code, clar=None):
    return cl.implementation_honours_decisions(am.HONOURED).evaluate(
        {"requirements": REQS, "clarifications": clar or decisions(), "implementation": code})


def test_code_that_follows_the_decisions_passes():
    assert honours(CODE) == []


def test_code_that_ignores_a_decision_is_flagged_with_expected_and_actual_values():
    out = honours(CODE.replace("DEFAULT_TTL_S = 90 * 24 * 3600", "DEFAULT_TTL_S = 7 * 24 * 3600"))
    assert len(out) == 1 and "7776000" in out[0].message and "604800" in out[0].message


def test_a_missing_constant_and_a_flipped_alias_rule_are_flagged():
    assert any("is missing" in v.message for v in honours(CODE.replace("MAX_TTL_S", "MAXIMUM_TTL_S")))
    assert any("DEFAULT_TTL_APPLIES_TO_ALIASES" in v.message for v in honours(CODE.replace("APPLIES_TO_ALIASES = False", "APPLIES_TO_ALIASES = True")))


def test_unresolved_decisions_are_skipped_by_the_code_check_but_missing_code_is_not():
    assert honours(CODE, decisions(default_ttl_days=(None, "unresolved"))) == []
    assert cl.implementation_honours_decisions(am.HONOURED).evaluate({"requirements": REQS, "clarifications": decisions()})


def test_the_gate_wrappers_are_wired_to_the_right_artifact_keys():
    ok, why = g.clarification_gate(am.RULES)({"requirements": {"content": REQS}}, {"content": decisions()})
    assert ok, why
    ok, why = g.decisions_honoured(am.HONOURED)({"requirements": {"content": REQS}, "clarifications": {"content": decisions()}},
                                                {"content": CODE})
    assert ok, why
