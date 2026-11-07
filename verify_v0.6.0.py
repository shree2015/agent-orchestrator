"""Check that THIS folder holds exactly the files of Agent Orchestrator v0.6.0.

Run it from your project folder (the one that contains README.md and orchestrator/):
    python verify_v0.6.0.py
It reads files only; it changes nothing. Line endings (CRLF/LF) are ignored.
"""
import hashlib
import sys
from pathlib import Path

MANIFEST = {
 ".gitattributes": "3c19801e7887aeb8f4a61a2f63ba462d354823218312ff03a287c9b98c900edc",
 ".gitignore": "c227a0a03fc71d3b313f52a468ef5c6f9a62a95184ff26953c29e35b058998c4",
 "CHANGELOG.md": "ccdf3ee625d06c0c9fdff4c73a33f52eae6b76b6b65de78c62c2682c72208757",
 "README.md": "8ac31f22b390ee0162c883d781d02261702a561cf04b41fae5aa481adf15f97c",
 "docs/admin-page.md": "215c4552de54d6f10af3a99e1dfed2773d43a1c59c92b7b3f09098f2dd71e504",
 "docs/ai-usage-log.md": "9b28edad32355c48ea30f6dc5dbce82079c1036152b440bc6c741e85da1acb32",
 "docs/architecture-reference.md": "9c655cbcb2f7a9013bd305766ae4bff6c8fa62d2d146c79323f1e006b6d9b8b7",
 "docs/architecture.md": "c173dc9f0bf95eb65a092aaf215ff350bc8e21756b5e6107d5b2553d9399f5a4",
 "docs/architecture.pdf": "50a457914ddb6aefd16a8976c8aadb74f8e9a98b9e08bd309cf7cd3dc759db26",
 "docs/decisions/001-resilience-ladder.md": "65b86aa3f03ef1137e5f92f283b7772860070db75b30afe434444a5e4d4ec595",
 "docs/decisions/002-policy-as-code.md": "79775ff2bd5024bdbdf749ce15179354e7f4865ae8a002ad938273f66c45cd94",
 "docs/decisions/003-brownfield-declare-then-enforce.md": "11eb83e5739d169a48d14a47524d2cfb8309dd1960f94e215e377cef5d29b1ae",
 "docs/decisions/004-fingerprint-based-replanning.md": "b7a2e6b4b2046d9d0d661b2e4acd8e0f17aed7baa53198d1ce3094a90a8cdf88",
 "docs/decisions/005-human-decisions-and-traceability.md": "2f024ee1b2e7b8e07bdd524edb3c9bd9e44ed72c1f4b1b37f7b666ad16cb4593",
 "docs/decisions/006-real-llm-integration.md": "ade55c72840eb6b9ce9da5f6aec9ac5acaabef2124274be7662615b39ea7dcbe",
 "docs/decisions/007-admin-page.md": "f1656a4136dd09ecf9b5618b6724eabaa2cc869b05af9d424afb5fabe197e60a",
 "docs/decisions/008-launching-generated-services.md": "b67672575aee0c84972789ba8860860cd6d34ca1abad9d556523dc563263aac9",
 "docs/policies.md": "889955f30c6b411a391f25677f5aa64640fe1ef8f2122ca976e56c3e780fd5bb",
 "docs/real-llm.md": "35e451f1529ce152a964b8866b5508028a290dc2832d7b159b29e6f991323cb6",
 "docs/requirements-traceability.md": "14bf90c67a8c429f350d0a3e865144cb6c8e21017232a150107f730b68c4e07f",
 "docs/requirements-traceability.pdf": "2ceae04bd0f4bf2ae0d2b09dab86dc5e4f739b03e0af2a231d124498594c3624",
 "docs/scenarios/01-greenfield-url-shortener.md": "b159cd0bcde225c19ce6521e9cb7464fe7a7458bd6af1106cf91ce6cff2db98a",
 "docs/scenarios/02-brownfield-orphaned-clicks.md": "86eaa6dc61e178a8a5fe6e397475e9a68be3d1bc62f823b0201766c7fa445eab",
 "docs/scenarios/03-ambiguous-expiry.md": "01c20e4b86598a71dac5fb9289d42443b20c79b4f290ad336d77ddcb7cc62c1a",
 "docs/testing.md": "598342aaded14140318b6bf9d10d44baa5ba0a33a45a40fbb12605e833f2cbab",
 "examples/requirements/url-shortener.md": "a7916ae053a112e032fe2362eb5cfd22b454650a21509717b6cdaeda10aa5ca5",
 "orchestrator/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
 "orchestrator/admin/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
 "orchestrator/admin/__main__.py": "e3739a79fddd51f4fcdfd5b671ee2f407b6597bd4972635f9392ae55b9988fd4",
 "orchestrator/admin/admin.html": "88a27f74261344801567a9084a3eca1109c308298b878cca5042f00d0bcd1400",
 "orchestrator/admin/scenarios.py": "ce39f0d597680b3c8f25b4a454ada206c31cd0c26d6bc489882fc569d10b5bc5",
 "orchestrator/admin/security.py": "cd27f8439481a297c303cc1c9e07aa82ec90082bb7e9364cc1644ee5c0554f4c",
 "orchestrator/admin/server.py": "761f4c1daa521c71e2eca4c596a656ccd2ec2d2eadd511b1bae584a07ecc1dda",
 "orchestrator/admin/service.py": "d67b6a2b57dee12d30f90550fed671377a973d33b06fc9854115fa4d0b174bbf",
 "orchestrator/admin/session.py": "213d3c56cdee6baf7c81831bf3a19887e0423baccba12fa7ffde37d61974d30b",
 "orchestrator/agents/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
 "orchestrator/agents/llm_agent.py": "4ce53020ac8f5bcfda06d861ceadb3b1f29a60d49c9216dabc1a2bb23f81c38e",
 "orchestrator/cli.py": "e78abe229db846f39405c4d6ce72af01ec95acaccfae65acb56ca573178f5ae6",
 "orchestrator/core/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
 "orchestrator/core/engine.py": "f897bbd4ca480b683e12b7d1da6c53dc9b122f0217582a7d36076af2b7f08d75",
 "orchestrator/core/graph.py": "ded439c2b9655955b8bd18a7069271acd9f861ebd30e527413943446ef7e9231",
 "orchestrator/core/state.py": "5d434ed7527768cd5a85e32891725d92195035daba87e10fc72d3ae3a6f390e8",
 "orchestrator/governance/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
 "orchestrator/governance/approvals.py": "370f3e8507d0dfdca20489bde996c1c49f089b369a9c07bb6d415373012d2850",
 "orchestrator/governance/codeintel.py": "cb98d0b32af6e29baa656dd23f2360f5510268c4f9c36c2387e694be5ec41bc0",
 "orchestrator/governance/gates.py": "d32702d53ea4815623edfcbaa231ecb00613b31678a676080b3673aa86c51d58",
 "orchestrator/governance/policies/__init__.py": "8ad01a12c60d3144dee6b4897732b6d0957d85e271ad103c297077fc11ba8e86",
 "orchestrator/governance/policies/base.py": "05816ca19fb69866e8fb1cba44d1eee30aed0e01dc8bd7030aa1220de41cdbb4",
 "orchestrator/governance/policies/change_control.py": "06e7508570a148b3c549ab011ed3a22f11eb5c25b1844a61e377f65f6c6fce2e",
 "orchestrator/governance/policies/clarification.py": "e5a8d3963001ea32224b0271dfe40f5f4d01e6166949ec7c2975f15d8584cf07",
 "orchestrator/governance/policies/compliance.py": "ddf840a0c29ae8e913d5038f14ca8b2bb22f670eecc826d02743aca85e4b15d4",
 "orchestrator/governance/policies/planning.py": "f9daea3ca1d69e94ebe30c3d288ff32bdfdc0fdca236af161b4d2edd1707a9ee",
 "orchestrator/governance/policies/security.py": "52291f4ddf9ee4d26e19e16d873affa9d655ad1f9af28146e1bcd9cb59ee0208",
 "orchestrator/governance/policy_engine.py": "91c352b4932b728beb39f84c268eb0f3ce4d8599ec2ed83ecdd6e7e182b6a0a0",
 "orchestrator/governance/sandbox.py": "09bd9add7bbdf7d7934a646e2be1e3862121ae282037a38c87ffd16eff6a228f",
 "orchestrator/llm/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
 "orchestrator/llm/anthropic_client.py": "97b134ec4ffea3ca51d251eba46d2a84a6b462dac6f8314168eecd15417bfe07",
 "orchestrator/llm/base.py": "ec4284c6e3a55918f754b83a9ae04b30d6ed0380be727ebc190456ac3b5594f8",
 "orchestrator/llm/contracts.py": "1b4aeb1e7d8747d852f7f652618ede4eafd4212b1a8bbfe7c90352fbe26b75f3",
 "orchestrator/llm/mock_client.py": "3297868e81af0d497534012b5e1b8c47bab7013f5ba2a7764e5b7d3f9ffd9cfa",
 "orchestrator/llm/routing.py": "7ecca87f6d5d9d7edb762e5d9cb3eeb44d4f0624e424394d11d082b2c7854d7a",
 "orchestrator/observability/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
 "orchestrator/observability/audit_log.py": "df8f11c564861b59132ac6f791f13fec854a017223b4ef3d7556b25fb2e3589d",
 "orchestrator/observability/metrics.py": "e3b463d81d7c3615fa8dcdc28ca65baa6f9d6b9ae2eed69d9121eb9c6a274ac6",
 "orchestrator/runner.py": "08192349a43e6f143e0b2f2477febbc0a2b872a308a8cacb5c6e6b9bc968e694",
 "orchestrator/workflows/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
 "orchestrator/workflows/ambiguous.py": "81491c01ed4f2ef56affad29bff72acf482604ecc0f63ef55c1e2ccb67c41b0a",
 "orchestrator/workflows/brownfield.py": "9022aab2ce87af609c854bdabdf34aeeae5d5f36c476220fd6e5b18836f54501",
 "orchestrator/workflows/plan_fixtures.py": "d3e9aafb16ecf325d470425328b02a913f7d11d3302fe67acc00019ece9d6618",
 "orchestrator/workflows/reference/ambiguous/impact_analysis.json": "9cc2b56dd98a1ca1e21491421e46ad64d5ca7dcd9e79c1852adfc497926fbcf1",
 "orchestrator/workflows/reference/ambiguous/plan.json": "3864464ff915cd25b82cfcf570709dcd2199808a668e3270bb631d218466504d",
 "orchestrator/workflows/reference/ambiguous/request.md": "dc37f924ee06238d398f1af5b0fc4d8fc6a977714d90328b4d5af8fc04561de0",
 "orchestrator/workflows/reference/ambiguous/requirements.json": "8674e01fd26818caf0a0e6fe94b244efac59cd6d30f296c681d1ecd672b5a8b9",
 "orchestrator/workflows/reference/brownfield/baseline_shortener.py.txt": "babbc36125229bfff6e81fc2261c216527ac2e24eb17c3ab380710ef7380d402",
 "orchestrator/workflows/reference/brownfield/baseline_tests.py.txt": "1b72c3a1df0f5e687e3f81a09bb0f7800da63cad985799a3f7ca2d71c19a1c2d",
 "orchestrator/workflows/reference/brownfield/bug_report.md": "9ada2f032a66ec2509254cd91feddb5541f51706387ebca1edf0c6476f922a66",
 "orchestrator/workflows/reference/brownfield/changelog.md": "62f52809147d1954b1f1a01f18cf45210509b2298befaab3c40e7495245fcdbe",
 "orchestrator/workflows/reference/brownfield/design.md.txt": "6f38e13230930dc51b501641754f4599bee8fa5198570d231d621e1e17453a60",
 "orchestrator/workflows/reference/brownfield/fixed_shortener.py.txt": "dd21e8dab7153a5ed3d8640d5371cb6d103a2b8595aac18309be9786106e0d9b",
 "orchestrator/workflows/reference/brownfield/fixed_tests.py.txt": "91a6e74b2f3a3bb1010ac310715153a16820772e68456286502759f892ee128c",
 "orchestrator/workflows/reference/brownfield/impact_analysis.json": "0d11c5e37cde752f0ed80f8d1147808ea8e11af8e1561eb672aaed5d93e8facc",
 "orchestrator/workflows/reference/brownfield/requirements.json": "7ee8258bb97a7954dbb4901747680f496e88e6aeb55383e06f4769d88e79ccdb",
 "orchestrator/workflows/reference/shortener/design.md.txt": "7b3bef33ec4558dae72eb29033f2c64fdc9ddba226eece11931503ae59d87bab",
 "orchestrator/workflows/reference/shortener/docs.md": "c150f8a3fe8573b3a405742611f34933d9a031aeefa09811ccb59a4e7603dec4",
 "orchestrator/workflows/reference/shortener/plan.json": "05604e91997423d26c221541ba4292c8a100a2332fd14da77f3ae8f37c333b93",
 "orchestrator/workflows/reference/shortener/requirements.json": "2962cfc097859faf31452b0ebf22126601ffe45b40938ae2aff4c108449d1a98",
 "orchestrator/workflows/reference/shortener/shortener.py.txt": "816c59bb2b8f9478f9316a72b43b0aa1e28a948a77b4b2a844d977c9191843eb",
 "orchestrator/workflows/reference/shortener/tests.py.txt": "6db6a89493867978496360ee0c2142b0da5cc039292e5e251d31bb243b1f062e",
 "orchestrator/workflows/reference/shortener/ui.html.txt": "8428df0b2a76d0e8fd77bbe7191f9816f177b42d5d36a4aa06cec56e290fec10",
 "orchestrator/workflows/shortener.py": "0a80b6bab8a9cbdcc84cff68f79477b9fea2e989d688f06055390d00b2766197",
 "orchestrator/workflows/slugify.py": "5c08361164f5c645836ae59d5b104d39670ce61881cd46cbb150268deab302f8",
 "pytest.ini": "6c6ccf864e8f2d2e0223ee8e39bfde660c12435aa37d81e2b14000a6bee15003",
 "requirements-dev.txt": "646e80d8359948d1ee7bc4db5c079f9e314c43f4303c7e6785bbb06224200328",
 "scripts/build_docs_pdf.py": "db6d861ee43745e6e416dd500f76404e2861fdf9e79fb35759d2c07a0e6d0b2d",
 "scripts/gen_architecture_docs.py": "fded5567cfc6ebfb23a0260453e37ca07e4b1db03437f038b2518a0234ca1fe6",
 "scripts/html_to_pdf.js": "4f4a7233c1a170ccadb3a4eedd6be820066f71171ce1cfc60e61b5ccbb0d33c2",
 "scripts/make_link.py": "8a6f1352ff9d16c6db47267d158a9931fa08cf15c2b4acbed20c5a23b23d3c6c",
 "scripts/run_shortener_tests.py": "20eea01351edfd6e3cc9622b4545c8afecf92f7392757e8eb6d2494848a08b8e",
 "scripts/smoke_test.py": "937b5ba984d41bcb875a47e9bb755320678b50fd00ed982e5baa8cc75854a74a",
 "simple_graph/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
 "simple_graph/demo.py": "0978828afafca0e0478d8371671d1c912ea1c886d0044644670acd1fa4ebd421",
 "simple_graph/sdlc_graph.py": "867a494a80161c481a17f6a156344adca53fa8269fe6d345f2ca6a3e647ec716",
 "tests/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
 "tests/admin_harness.js": "78df06cd6c34ca0314d58a191dcf1d99c6da8577e892beee7a8fedce002d7b01",
 "tests/test_admin_api.py": "bf16acf8654d2fe91c9457ebcef8a318da03bedeef2bd6c69496a58aca52a2ed",
 "tests/test_admin_service.py": "e55361506693444543e33194111fa8458e78a6aeae9d5e1396da61b36c06093a",
 "tests/test_admin_ui.py": "94b68efcebd5a78a97a780eeb962a49cead9ce29f7c78163f5dd6eeda9884c87",
 "tests/test_ambiguous.py": "c26b50479d1e323ac75506f302c43c158ea3dd5152d47fecf34b207f50947dad",
 "tests/test_brownfield.py": "49297daf981acbce7aa6a1d496430478b529967d35c02aa3a388eaffc6c57771",
 "tests/test_clarification.py": "ff4598cf171fc567a3e9b2ad98f90258d14b4dbf385e46fd225b4f776c431112",
 "tests/test_codeintel.py": "9fa402947c7142e1d9845df3cfc9f384ee921f16a9839eb2222f6b5165f0bbcd",
 "tests/test_core.py": "fd98890c95efb8d41f2345f6c614250324408d0bb566e7b8c67aa5e0f6f5d48c",
 "tests/test_docs.py": "dd0270660bd2e859938e55c2cae078c40c98f200dc2f1e9f46c4b41e01eb6ab2",
 "tests/test_planning.py": "e0328debe4626440bad78c7a6068c4e5a94fe2ced18717a654b633798af966e7",
 "tests/test_policies.py": "7cf005349cf962e484f9c3a96f0eb4fbaf5ca384c6c4091d0319fd4a416fed0e",
 "tests/test_real_llm.py": "57765d652fd4f2a84d78ca657cdf59749a64bfcfd8d85ca0225af114b9920f2e",
 "tests/test_replan.py": "12b13df24f4c013aec9fc3eae59c449575ad49fde476ffdc3481a63f7439fc1c",
 "tests/test_request_bodies.py": "cb71c4b899e394368192505e08472329b93a8ee87ccf74f78b524197ef50acc2",
 "tests/test_resilience.py": "982656da621b9906134f0113f999011ad789680bbd080d9d978ea3377a4fa433",
 "tests/test_shortener_service.py": "4533f3eeb0e788afd8776f30d3ec98ecd67481a0295430beab96e96bb4440b8d",
 "tests/test_shortener_workflow.py": "2efcb3bf4e82aa6719c194405c20bb16579e722c2b2118ef54c6a3c91c6f6128",
 "tests/test_simple_graph.py": "d55116d470d5934bde623676c46539a7af9f9a7086d1d7ec5da20bbf4181e19d",
 "tests/test_slice.py": "690a01f105c1f47de380f83de9bac82f905ee2b0ef5e4b2df63747a73a53ae3b",
 "tests/test_smoke_script.py": "e13553cafb1c6362860f28226be7b5ffc07c423936ad811c0142ad4c876416f9",
 "tests/test_ui.py": "d2677ce15bdaa26d48cdeaf1c6dd223b38be7a91038730e2ff13e5cd218d4e5b",
 "tests/ui_harness.js": "0c9d7dddcb06bc640c4a26a5c630d3eaa784ffbba8b30bce447b8bc5e8788230"
}

SKIP = {".git", "venv", ".venv", "runs", "llm-records", "__pycache__", ".pytest_cache", "node_modules", ".mypy_cache"}


def digest(path):
    data = path.read_bytes()
    return hashlib.sha256(data if path.suffix == ".pdf" else data.replace(b"\r\n", b"\n")).hexdigest()


def main():
    root = Path(".")
    if not (root / "orchestrator").is_dir() or not (root / "README.md").is_file():
        sys.exit("Run this from the project folder (it must contain README.md and the orchestrator folder).")
    present = {}
    for f in root.rglob("*"):
        rel = f.relative_to(root)
        if f.is_file() and not (set(rel.parts) & SKIP) and not f.name.endswith((".db", ".db-shm", ".db-wal", ".pyc")):
            present[str(rel).replace("\\", "/")] = f
    missing = sorted(set(MANIFEST) - set(present))
    different = sorted(p for p in MANIFEST if p in present and digest(present[p]) != MANIFEST[p])
    extra = sorted(set(present) - set(MANIFEST))
    print(f"{len(MANIFEST) - len(missing) - len(different)} of {len(MANIFEST)} files match v0.6.0")
    for title, items in (("MISSING (v0.6.0 has it, you do not)", missing), ("DIFFERENT (content differs from v0.6.0)", different),
                         ("EXTRA (you have it, v0.6.0 does not)", extra)):
        if items:
            print(f"\n{title}: {len(items)}")
            for p in items:
                print("  ", p)
    if not (missing or different or extra):
        print("\nYour folder is EXACTLY v0.6.0.")
    return 0 if not (missing or different) else 1


if __name__ == "__main__":
    raise SystemExit(main())
