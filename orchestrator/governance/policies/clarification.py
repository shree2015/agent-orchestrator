"""Clarification policies: ambiguity must be resolved by the right party, and the answers must reach the code.

Subjects are artifact mappings:
  requirements   JSON: {"ambiguities": [{"id", "question", "options": [...], "risk": "high|medium|low", "proposed", "why"}], ...}
  clarifications JSON: {"decisions": [{"id", "answer", "decided_by": "human|assumed|unresolved", "rationale"?}]}
FAIL CLOSED everywhere: no decisions artifact means no pass.
"""
from __future__ import annotations

import json
from typing import Callable, Mapping

from .. import codeintel as ci
from .base import Policy, PolicySet


def _load(subject: Mapping[str, str]):
    """Return (ambiguities, decisions_by_id, errors)."""
    try:
        amb = json.loads(subject.get("requirements") or "")["ambiguities"]
        dec = json.loads(subject.get("clarifications") or "")["decisions"]
        if not all(isinstance(a, dict) and a.get("id") for a in amb) or not all(isinstance(d, dict) and d.get("id") for d in dec):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        return [], {}, ["requirements (ambiguities) and clarifications (decisions) must both be present and valid JSON"]
    return amb, {d["id"]: d for d in dec}, []


def decisions_cover_ambiguities() -> Policy:
    def check(subject):
        amb, dec, errors = _load(subject)
        if errors:
            return errors
        ids = {a["id"] for a in amb}
        return ([f"no decision recorded for ambiguity '{i}'" for i in sorted(ids - set(dec))]
                + [f"decision '{i}' answers a question that was never asked" for i in sorted(set(dec) - ids)])
    return Policy("decisions-cover-ambiguities", "compliance", "Every open question gets a recorded decision", check)


def high_risk_needs_human() -> Policy:
    def check(subject):
        amb, dec, errors = _load(subject)
        if errors:
            return []
        out = []
        for a in amb:
            if a.get("risk") == "high":
                d = dec.get(a["id"], {})
                if d.get("decided_by") != "human" or d.get("answer") is None:
                    out.append(f"'{a['id']}' is high-risk and needs a human decision: {a.get('question', '')}")
        return out
    return Policy("high-risk-needs-human", "compliance", "Agents may not assume answers to high-risk questions", check)


def answers_within_options() -> Policy:
    def check(subject):
        amb, dec, errors = _load(subject)
        if errors:
            return []
        return [f"answer {dec[a['id']].get('answer')!r} for '{a['id']}' is not one of the allowed options {a.get('options')}"
                for a in amb if a["id"] in dec and dec[a["id"]].get("answer") is not None
                and dec[a["id"]]["answer"] not in a.get("options", [])]
    return Policy("answers-within-options", "compliance", "Answers must be one of the supported options", check)


def assumptions_have_rationale() -> Policy:
    def check(subject):
        _, dec, errors = _load(subject)
        if errors:
            return []
        return [f"assumption for '{i}' needs a rationale" for i, d in dec.items()
                if d.get("decided_by") == "assumed" and not (d.get("rationale") or "").strip()]
    return Policy("assumptions-have-rationale", "compliance", "An assumed answer must say why", check)


def decisions_consistent(rules: list[tuple[str, Callable[[dict], bool], str]]) -> Policy:
    """rules: (decision ids needed, predicate(answers) -> bool, message). Skipped while a needed answer is missing."""
    def check(subject):
        _, dec, errors = _load(subject)
        if errors:
            return []
        answers = {i: d.get("answer") for i, d in dec.items()}
        out = []
        for needs, ok, msg in rules:
            if not all(answers.get(n) is not None for n in (needs if isinstance(needs, (list, tuple)) else [needs])):
                continue
            try:
                if not ok(answers):
                    out.append(msg.format(**answers))
            except Exception as exc:                  # fail closed: an uncomparable answer is a violation, not a crash
                out.append(f"answers could not be compared ({type(exc).__name__}): {msg.format(**answers)}")
        return out
    return Policy("decisions-consistent", "compliance", "Answers must not contradict each other", check)


def implementation_honours_decisions(mapping: dict[str, tuple[str, Callable]]) -> Policy:
    """decision id -> (module constant name, transform(answer) -> expected value). Subject carries the code."""
    def check(subject):
        _, dec, errors = _load(subject)
        if errors:
            return errors
        if not (subject.get("implementation") or "").strip():
            return ["missing or empty artifact: implementation"]
        try:
            consts = ci.module_constants(subject["implementation"])
        except SyntaxError:
            return []
        out = []
        for did, (const, transform) in mapping.items():
            if did not in dec or dec[did].get("answer") is None:
                continue
            want = transform(dec[did]["answer"])
            if const not in consts:
                out.append(f"decision '{did}' = {dec[did]['answer']!r} is not implemented: constant {const} is missing")
            elif consts[const] != want:
                out.append(f"decision '{did}' = {dec[did]['answer']!r} requires {const} == {want!r}, but the code sets {consts[const]!r}")
        return out
    return Policy("implementation-honours-decisions", "compliance", "Human decisions must be visible in the code", check)


def clarification_set(rules) -> PolicySet:
    return PolicySet(decisions_cover_ambiguities(), high_risk_needs_human(), answers_within_options(),
                     assumptions_have_rationale(), decisions_consistent(rules))
