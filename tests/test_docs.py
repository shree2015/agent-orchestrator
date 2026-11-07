"""The architecture documentation must stay true to the code.

Generated parts (workflow graphs, the controls on every stage, the policy and event catalogues, the worked example, counts quoted in
prose) are rebuilt from the real code and compared; every file the documents name must exist; every policy in the code must be
described in policies.md; every Mermaid diagram must be well-formed.
"""
import glob
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import gen_architecture_docs as gen  # noqa: E402

DOCS = ROOT / "docs"
CHECKED = [DOCS / "architecture.md", DOCS / "architecture-reference.md", DOCS / "requirements-traceability.md", ROOT / "README.md"]


def test_generated_documentation_is_up_to_date():
    stale = [str(p.relative_to(ROOT)) for p, text in gen.expected_outputs().items() if p.read_text(encoding="utf-8") != text]
    assert not stale, f"out of date: {stale}. Run: python scripts/gen_architecture_docs.py"


def test_generated_blocks_are_balanced_and_known():
    for path in (DOCS / "architecture.md", DOCS / "requirements-traceability.md"):
        text = path.read_text(encoding="utf-8")
        assert text.count("<!-- BEGIN GENERATED:") == text.count("<!-- END GENERATED:"), path.name
        assert text.count("<!--GEN:") == text.count("<!--/GEN-->"), path.name


def test_the_reference_covers_every_policy_event_and_workflow():
    ref = (DOCS / "architecture-reference.md").read_text(encoding="utf-8")
    for p in gen.policy_registry():
        assert f"`{p.id}`" in ref, f"policy {p.category}/{p.id} missing from the generated catalogue"
    for name in gen.EVENT_MEANING:
        assert f"`{name}`" in ref, f"event {name} missing from the generated catalogue"
    for _, _, title in gen.WORKFLOWS:
        assert title in ref
    assert "mttr_s" in ref and "node_success_rate" in ref and "end_to_end_latency_s" in ref


def test_every_policy_in_the_code_is_described_in_policies_md():
    text = (DOCS / "policies.md").read_text(encoding="utf-8")
    missing = [f"{p.category}/{p.id}" for p in gen.policy_registry() if p.id not in text]
    assert not missing, f"policies.md does not mention: {missing}"
    for rule in gen.LINT_RULES:
        assert rule in text, f"workflow lint rule {rule} is not in policies.md"


_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_TICK = re.compile(r"`([^`\n]+)`")
_PATHLIKE = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+/?$")
_ROOTS = {"orchestrator", "tests", "docs", "scripts", "examples", "simple_graph"}
_RUNTIME = ("runs/", "llm-records")                          # created when something runs, not part of the repository


def test_every_local_link_and_file_named_in_the_documents_exists():
    problems = []
    for doc in CHECKED:
        text = doc.read_text(encoding="utf-8")
        for target in _LINK.findall(text):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            if not (doc.parent / target.split("#")[0]).exists():
                problems.append(f"{doc.name}: broken link {target}")
        for token in _TICK.findall(text):
            if not _PATHLIKE.match(token) or token.startswith(_RUNTIME):
                continue
            first, last = token.split("/")[0], token.rstrip("/").split("/")[-1]
            if first in _ROOTS or "." in last:
                if not (ROOT / token).exists() and not (doc.parent / token).exists():
                    problems.append(f"{doc.name}: `{token}` does not exist")
    assert not problems, "\n".join(problems)


def test_the_traceability_document_covers_all_eight_requirements_and_all_criteria():
    text = (DOCS / "requirements-traceability.md").read_text(encoding="utf-8")
    core = text.split("## 1.")[1].split("## 2.")[0]
    assert [int(m) for m in re.findall(r"^\| (\d) \|", core, re.M)] == list(range(1, 9))
    criteria = text.split("## 4. Evaluation criteria")[1].split("## 5.")[0]
    assert len([ln for ln in criteria.splitlines() if ln.startswith("| ") and not ln.startswith(("| Criterion", "|---"))]) == 8
    for status in ("**Met**", "**Pending**"):
        assert status in text
    assert "Known gaps" in text                                                    # the gaps are stated, not hidden


def _mermaid_blocks():
    for doc in CHECKED:
        for i, block in enumerate(re.findall(r"```mermaid\n(.*?)```", doc.read_text(encoding="utf-8"), re.S), 1):
            yield doc.name, i, block


def test_every_mermaid_diagram_is_well_formed():
    count = 0
    for name, i, block in _mermaid_blocks():
        count += 1
        body = [ln for ln in block.splitlines() if ln.strip() and not ln.strip().startswith("%%")]
        assert body[0].split()[0] in ("flowchart", "sequenceDiagram", "stateDiagram-v2", "graph"), f"{name} #{i}: unknown diagram type"
        assert "\t" not in block, f"{name} #{i}: tab character"
        assert block.count("[") == block.count("]") and block.count("(") == block.count(")") and block.count("{") == block.count("}"), f"{name} #{i}: unbalanced brackets"
        assert block.count('"') % 2 == 0, f"{name} #{i}: unbalanced quotes"
    assert count >= 9


def _mermaid_cli():
    mmdc = shutil.which("mmdc")
    chrome = (glob.glob("/opt/pw-browsers/chromium_headless_shell-*/*/chrome-headless-shell") or [None])[0]
    return (mmdc, chrome) if mmdc and chrome else None


@pytest.mark.skipif(_mermaid_cli() is None, reason="needs mermaid-cli and a Chromium (a developer tool, optional)")
def test_every_mermaid_diagram_really_parses_and_renders(tmp_path):
    mmdc, chrome = _mermaid_cli()
    cfg = tmp_path / "p.json"
    cfg.write_text('{"executablePath": "%s", "args": ["--no-sandbox"]}' % chrome)
    for name, i, block in _mermaid_blocks():
        src = tmp_path / f"{name}-{i}.mmd"
        src.write_text(block, encoding="utf-8")
        r = subprocess.run([mmdc, "-i", str(src), "-o", str(tmp_path / f"{name}-{i}.png"), "-p", str(cfg)], capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, f"{name} #{i} does not render: {r.stderr[-300:]}"


def test_the_pdf_versions_exist_and_are_real_pdfs():
    for name in ("architecture.pdf", "requirements-traceability.pdf"):
        data = (DOCS / name).read_bytes()
        assert data.startswith(b"%PDF-") and len(data) > 20_000, name
