"""Check that THIS folder holds exactly the files of Agent Orchestrator v0.8.0.

Run it from your project folder (the one that contains README.md and orchestrator/):
    python verify_v0.8.0.py
It reads files only; it changes nothing. Line endings (CRLF/LF) are ignored.
Keep it OUTSIDE the repository (it is a helper, not part of the project).
"""
import hashlib
import sys
from pathlib import Path

MANIFEST = {
 ".gitattributes": "3c19801e7887aeb8f4a61a2f63ba462d354823218312ff03a287c9b98c900edc",
 ".gitignore": "c227a0a03fc71d3b313f52a468ef5c6f9a62a95184ff26953c29e35b058998c4",
 "CHANGELOG.md": "13ecab6298d706707665afa956bfc36ce5a0c812e4e3c5b7d785e5c52bab28e0",
 "README.md": "fafbc955ea56e00acca64c99fcbc80c131e65ed5f9189d2a36f2e9011217cee2",
 "docs/admin-page.md": "50b123006cb7552f64cfe2ccb6f06949f40b03860d0b9a1ae3a6da8ce6ea4fe0",
 "docs/ai-usage-log.md": "45e6157132579dd4c3c0f64b0125760b2dcd6cadabf2b35dfb61ff5f52d46495",
 "docs/architecture-reference.md": "0dfc676e929899d612011bb1e67bc236393ad3397e405a374f5378ac2688933b",
 "docs/architecture.md": "37fc993547fc828dda1ac75587fe9ed435ddb5898a06d1a62f493ed7f9fe07ee",
 "docs/architecture.pdf": "a86a534040d8d40b0fd7b99649d3f594054ce628d596825cbc65487e75f6b625",
 "docs/decisions/001-resilience-ladder.md": "65b86aa3f03ef1137e5f92f283b7772860070db75b30afe434444a5e4d4ec595",
 "docs/decisions/002-policy-as-code.md": "79775ff2bd5024bdbdf749ce15179354e7f4865ae8a002ad938273f66c45cd94",
 "docs/decisions/003-brownfield-declare-then-enforce.md": "45f812a9f4a0f3ddac327eb4ce1be7d5af825160c2ce151e09fcbcfcb2a7df7c",
 "docs/decisions/004-fingerprint-based-replanning.md": "b7a2e6b4b2046d9d0d661b2e4acd8e0f17aed7baa53198d1ce3094a90a8cdf88",
 "docs/decisions/005-human-decisions-and-traceability.md": "2f024ee1b2e7b8e07bdd524edb3c9bd9e44ed72c1f4b1b37f7b666ad16cb4593",
 "docs/decisions/006-real-llm-integration.md": "ade55c72840eb6b9ce9da5f6aec9ac5acaabef2124274be7662615b39ea7dcbe",
 "docs/decisions/007-admin-page.md": "f1656a4136dd09ecf9b5618b6724eabaa2cc869b05af9d424afb5fabe197e60a",
 "docs/decisions/008-launching-generated-services.md": "b67672575aee0c84972789ba8860860cd6d34ca1abad9d556523dc563263aac9",
 "docs/decisions/009-review-driven-rework.md": "487facdaae1915b9617f1b2fc5f016a9cbb1dba1864d152cc40d6f71e190cd89",
 "docs/final-summary.md": "0ccf312f0beddfcb3aed7960ac1b1777def6efaa10fd539ecf8441f196ad1513",
 "docs/final-summary.pdf": "4f0cb7c620ebf5b567430e020ff6093a4ef3a233ee61344d2da2dd377a7c31b5",
 "docs/policies.md": "237fc8286caa36e7ed708101717369e0639f1c0a574313c8639cd6784146557c",
 "docs/real-llm.md": "d05dd82f1f3e4372bc402d0d1e2c4b9f60c089819a1a62796bc25c99060ac287",
 "docs/requirements-traceability.md": "c372c17002d6a301bce43c310a897ee86ff18186a3e3044012028fadd8964a9c",
 "docs/requirements-traceability.pdf": "84e50113a8a55e1111578201aa6acd37c3afd585e8d0fa01359c0d97048326be",
 "docs/scenarios/01-greenfield-url-shortener.md": "4ac1cbcea84fcaced4b3832afd3ecfd579bd14dfe9e3e647c88d885dd3e0db59",
 "docs/scenarios/02-brownfield-orphaned-clicks.md": "86eaa6dc61e178a8a5fe6e397475e9a68be3d1bc62f823b0201766c7fa445eab",
 "docs/scenarios/03-ambiguous-expiry.md": "01c20e4b86598a71dac5fb9289d42443b20c79b4f290ad336d77ddcb7cc62c1a",
 "docs/testing.md": "5d606559280d27a1a6afb47852c1a8cc87b79b3df782db63f7020b3839751e41",
 "examples/requirements/url-shortener.md": "a7916ae053a112e032fe2362eb5cfd22b454650a21509717b6cdaeda10aa5ca5",
 "orchestrator/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
 "orchestrator/admin/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
 "orchestrator/admin/__main__.py": "e3739a79fddd51f4fcdfd5b671ee2f407b6597bd4972635f9392ae55b9988fd4",
 "orchestrator/admin/admin.html": "445b964441571555adb2f5070f4d22a0e4fe363a28833d2297a668004149cce8",
 "orchestrator/admin/scenarios.py": "3f9d65dee4f4d07d885bbb28b219f210043d10a1a5d306618f690ad67fe2dbeb",
 "orchestrator/admin/security.py": "cd27f8439481a297c303cc1c9e07aa82ec90082bb7e9364cc1644ee5c0554f4c",
 "orchestrator/admin/server.py": "761f4c1daa521c71e2eca4c596a656ccd2ec2d2eadd511b1bae584a07ecc1dda",
 "orchestrator/admin/service.py": "d67b6a2b57dee12d30f90550fed671377a973d33b06fc9854115fa4d0b174bbf",
 "orchestrator/admin/session.py": "a3b97f4fda6242dccbb823e1ddca625254b1e57042f230494e9b1fc004c93a11",
 "orchestrator/agents/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
 "orchestrator/agents/llm_agent.py": "7edc862cc627690ead06f5fe681bc1f340376a4c1bbdf3eae04487614dfa380a",
 "orchestrator/cli.py": "25e6d11e0a64229b811553761200a86adb9e11c6803331500f08329a12a080ee",
 "orchestrator/core/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
 "orchestrator/core/engine.py": "3f04bd0f284d9364a75c40a4348af95be8015b15882b57c49abbd9f7b8e1a343",
 "orchestrator/core/graph.py": "c1a2043403dd050f90324789640fa8e4e4b994ce5432a64eb82230b8902283c9",
 "orchestrator/core/state.py": "5d434ed7527768cd5a85e32891725d92195035daba87e10fc72d3ae3a6f390e8",
 "orchestrator/governance/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
 "orchestrator/governance/approvals.py": "370f3e8507d0dfdca20489bde996c1c49f089b369a9c07bb6d415373012d2850",
 "orchestrator/governance/codeintel.py": "cb98d0b32af6e29baa656dd23f2360f5510268c4f9c36c2387e694be5ec41bc0",
 "orchestrator/governance/gates.py": "bc8bf87ab1d504a0facb80de1555f9502f5837ec48855abd79dddf9728f0610c",
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
 "orchestrator/llm/contracts.py": "6450d8fa7170125ae53d95e134d5ffdd3a0172dbc410ab79aab90feb3fec451a",
 "orchestrator/llm/mock_client.py": "3297868e81af0d497534012b5e1b8c47bab7013f5ba2a7764e5b7d3f9ffd9cfa",
 "orchestrator/llm/routing.py": "7ecca87f6d5d9d7edb762e5d9cb3eeb44d4f0624e424394d11d082b2c7854d7a",
 "orchestrator/observability/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
 "orchestrator/observability/audit_log.py": "df8f11c564861b59132ac6f791f13fec854a017223b4ef3d7556b25fb2e3589d",
 "orchestrator/observability/metrics.py": "634d59771f8fdb8d303cdd574a3f274b14bce0d151e458349d2dab82f1818313",
 "orchestrator/runner.py": "17284575f3f847fa23fb16638ec9952e94a28ede759789d3283398a3ec078c3c",
 "orchestrator/workflows/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
 "orchestrator/workflows/ambiguous.py": "19fcd2c556f4ee0ab3721b830fe31ffd4eafbfbf090e90311ad37ea1c2939a64",
 "orchestrator/workflows/brownfield.py": "4a720d743961a8aecd7e3088607f2bec61c78919acc1abb1e07efc6fe727ae18",
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
 "orchestrator/workflows/shortener.py": "38b9e46f970df9a252ad53bbe2074b5fa98a2fc6d5b3fa70bf33a96b17e32e16",
 "orchestrator/workflows/slugify.py": "9b607a28fbb5b2cfe79892322df7146a7dbae1f0802fd496c57a72b1609f66bc",
 "pytest.ini": "6c6ccf864e8f2d2e0223ee8e39bfde660c12435aa37d81e2b14000a6bee15003",
 "requirements-dev.txt": "646e80d8359948d1ee7bc4db5c079f9e314c43f4303c7e6785bbb06224200328",
 "scripts/build_docs_pdf.py": "de61e1f9d469a9474976a0bac553d0a24a8b778100e1053b85d8e7a4922de749",
 "scripts/gen_architecture_docs.py": "a43137bca92eba80e459be4ad09d03711afe60460b3451d2ecec75c0902cf2a4",
 "scripts/html_to_pdf.js": "4f4a7233c1a170ccadb3a4eedd6be820066f71171ce1cfc60e61b5ccbb0d33c2",
 "scripts/make_link.py": "8a6f1352ff9d16c6db47267d158a9931fa08cf15c2b4acbed20c5a23b23d3c6c",
 "scripts/run_shortener_tests.py": "20eea01351edfd6e3cc9622b4545c8afecf92f7392757e8eb6d2494848a08b8e",
 "scripts/smoke_test.py": "937b5ba984d41bcb875a47e9bb755320678b50fd00ed982e5baa8cc75854a74a",
 "simple_graph/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
 "simple_graph/demo.py": "0978828afafca0e0478d8371671d1c912ea1c886d0044644670acd1fa4ebd421",
 "simple_graph/sdlc_graph.py": "867a494a80161c481a17f6a156344adca53fa8269fe6d345f2ca6a3e647ec716",
 "tests/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
 "tests/admin_harness.js": "494668189ecec6e91ee82155ddc3b6012e0e7fd8a14f3138c28d62be18df0aec",
 "tests/test_admin_api.py": "b381e487949adba182ff9e19ff1d76dd5a2d4dfaf2d54c6bea4c513d1b18d569",
 "tests/test_admin_service.py": "e55361506693444543e33194111fa8458e78a6aeae9d5e1396da61b36c06093a",
 "tests/test_admin_ui.py": "f1d85bf3e53dce3b1760e7a28c5c62f55fbb8ccee12f207bcb28ebbfa4eb57e5",
 "tests/test_ambiguous.py": "c26b50479d1e323ac75506f302c43c158ea3dd5152d47fecf34b207f50947dad",
 "tests/test_brownfield.py": "49297daf981acbce7aa6a1d496430478b529967d35c02aa3a388eaffc6c57771",
 "tests/test_clarification.py": "ff4598cf171fc567a3e9b2ad98f90258d14b4dbf385e46fd225b4f776c431112",
 "tests/test_codeintel.py": "9fa402947c7142e1d9845df3cfc9f384ee921f16a9839eb2222f6b5165f0bbcd",
 "tests/test_core.py": "fd98890c95efb8d41f2345f6c614250324408d0bb566e7b8c67aa5e0f6f5d48c",
 "tests/test_docs.py": "abf7472d60fae88c58e4a554619550761162309a517ecf0238bbd766465c0a21",
 "tests/test_planning.py": "e0328debe4626440bad78c7a6068c4e5a94fe2ced18717a654b633798af966e7",
 "tests/test_policies.py": "7cf005349cf962e484f9c3a96f0eb4fbaf5ca384c6c4091d0319fd4a416fed0e",
 "tests/test_real_llm.py": "57765d652fd4f2a84d78ca657cdf59749a64bfcfd8d85ca0225af114b9920f2e",
 "tests/test_replan.py": "12b13df24f4c013aec9fc3eae59c449575ad49fde476ffdc3481a63f7439fc1c",
 "tests/test_request_bodies.py": "cb71c4b899e394368192505e08472329b93a8ee87ccf74f78b524197ef50acc2",
 "tests/test_resilience.py": "982656da621b9906134f0113f999011ad789680bbd080d9d978ea3377a4fa433",
 "tests/test_review.py": "2763a5e17d2001653f4e916e855219e6f4a7f548ee227e78547c95b1fb28702a",
 "tests/test_rework.py": "9b2727a2a4c9ddd40289f58151d2739516da793a4c2211d95b026032bc9d14ed",
 "tests/test_shortener_service.py": "4533f3eeb0e788afd8776f30d3ec98ecd67481a0295430beab96e96bb4440b8d",
 "tests/test_shortener_workflow.py": "2efcb3bf4e82aa6719c194405c20bb16579e722c2b2118ef54c6a3c91c6f6128",
 "tests/test_simple_graph.py": "d55116d470d5934bde623676c46539a7af9f9a7086d1d7ec5da20bbf4181e19d",
 "tests/test_size_limits.py": "168cdaffe453f0ef7fbc42c5797664297cb0c3294ed71ef824ad1e06c745192c",
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
    print(f"{len(MANIFEST) - len(missing) - len(different)} of {len(MANIFEST)} files match v0.8.0")
    for title, items in (("MISSING (v0.8.0 has it, you do not)", missing), ("DIFFERENT (content differs from v0.8.0)", different),
                         ("EXTRA (you have it, v0.8.0 does not)", extra)):
        if items:
            print(f"\n{title}: {len(items)}")
            for p in items:
                print("  ", p)
    if not (missing or different or extra):
        print("\nYour folder is EXACTLY v0.8.0.")
    return 0 if not (missing or different) else 1


if __name__ == "__main__":
    raise SystemExit(main())
