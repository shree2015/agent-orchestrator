# Scenario 2: Brownfield - fix BUG-142 in the existing shortener

**Input:** a support ticket (`orchestrator/workflows/reference/brownfield/bug_report.md`): after deleting a link and
re-creating the same alias, analytics show the *old* link's clicks. Constraints: no API change, small reviewable
change, and existing data must be repaired.

**Run it:**
```
python -m orchestrator.cli run brownfield --approve auto
python -m orchestrator.cli run brownfield --approve auto --violate scope-creep
```

## 1. Codebase reasoning (what the analysis found)
| | |
|---|---|
| Root cause | SQLite enforces foreign keys per connection and only after `PRAGMA foreign_keys = ON`. `LinkRepository` never issues it, so the declared `ON DELETE CASCADE` never runs and `delete()` leaves click rows behind. |
| Symptom path | `POST alias` -> `LinkRepository.add` -> `stats(code)` aggregates by `code` alone, so a re-created alias inherits old clicks. |
| Impacted symbols | `LinkRepository.__init__`, `LinkRepository.delete` (changed); `LinkRepository.stats`, `ShortenerService.delete`, `ShortenerService.shorten` (reviewed, unchanged). |
| APIs affected | `DELETE /api/links/{code}`, `GET /api/links/{code}/stats`, `POST /api/shorten` (alias reuse). |
| Data impact | Existing databases may already hold orphaned clicks, so code alone is not enough: a startup migration is needed. |

## 2. Decomposition
```
requirements -> impact_analysis -> design* -> implementation -> tests  ->\
                                                             -> docs   ->  review -> release*
```
Compared with greenfield: an `impact_analysis` stage comes first, and the gates judge the change **against the
baseline** instead of judging new code in isolation.

| Node | What is enforced |
|---|---|
| impact_analysis | Valid JSON with root cause, impacted symbols and risks; **every symbol named must exist in the real baseline** (`impact-analysis-grounded`) |
| implementation | Security + documentation policies; at most 1 file and 40 changed lines; **public API stays backward compatible** (`public-api-stable`); **may only touch the symbols the analysis declared** (`change-within-declared-impact`) |
| tests | All pass on the fix; none of the existing tests removed (`no-tests-removed`); **at least one new test must FAIL on the old code** (`regression-test-reproduces-bug`) |
| docs | Changelog must reference BUG-142 (`changelog-references-ticket`) |
| release | Entry gate re-verifies all evidence including the impact analysis, then human approval |

## 3. The fix (10 changed lines, 3 symbols)
1. `LinkRepository.__init__`: keep foreign-key enforcement on, then call `_purge_orphans()` once at startup.
2. `LinkRepository.delete`: delete the link's clicks explicitly in the same transaction (never rely on the pragma alone).
3. `LinkRepository._purge_orphans` (new, private): `DELETE FROM clicks WHERE code NOT IN (SELECT code FROM links)`; idempotent.

## 4. Validation evidence (checked by `tests/test_brownfield.py`)
* The buggy baseline passes **all 31 existing tests**: the old suite could not see the bug.
* The 3 new regression tests **fail on the baseline** (exactly those 3) and **pass on the fix** (34 pass).
* The actual diff touches exactly the 3 declared symbols.

## 5. Guardrails demonstrated (each `--violate` makes one agent misbehave once, then recover)
| Flag | What the agent does wrong | Who stops it |
|---|---|---|
| `scope-creep` | Quietly retunes an unrelated limiter | `change_control/change-within-declared-impact` names the symbol |
| `api-break` | Adds a required parameter to `get_link` | `change_control/public-api-stable` (and the declared-impact policy) |
| `ungrounded-impact` | Analysis names a method that does not exist | `compliance/impact-analysis-grounded`, before any code is written |
| `no-regression-test` | Resubmits the old tests, no new test | `compliance/regression-test-reproduces-bug` |
| `drop-tests` | Deletes an inconvenient existing test | `compliance/no-tests-removed` |
| `changelog-no-ticket` | Changelog does not mention the ticket | `compliance/changelog-references-ticket` |

Also covered by tests: an oversized patch is blocked by the 40-line budget; a developer who keeps breaking the API
is replaced by the fallback agent; a failed re-plan rolls back to the verified fix.

## 6. Risks and trade-offs
| Risk | Mitigation | Residual |
|---|---|---|
| The purge deletes data | Only rows with no parent link (unreachable through any API); idempotent | Irreversible; production should snapshot first |
| Declared-impact check is symbol-level | Catches any edit outside declared functions, methods, class bodies and module level | Cannot detect a harmful change *inside* a declared symbol; that is the reviewers' and tests' job |
| Signature checks are syntactic | Detects removed, reordered and newly required parameters | Does not detect behavioural breaks with unchanged signatures |
| Agents are offline mocks | Gates judge real code regardless of its source | Generation quality is not demonstrated |
