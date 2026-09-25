from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_extension_supports_evidence_and_masks_password_values() -> None:
    content = (ROOT / "extension" / "content.js").read_text(encoding="utf-8")
    controller = (ROOT / "extension" / "agent.js").read_text(encoding="utf-8")
    assert 'name === "read_page"' in content
    assert "evidence:" in content
    assert 'getAttribute("type") === "password"' in content
    assert "last_result: lastResult" in controller
    assert "agentState = decision.state" in controller


def test_extension_runtime_has_no_acceptance_site_hardcoding() -> None:
    runtime = "\n".join(
        (ROOT / "extension" / name).read_text(encoding="utf-8")
        for name in ("agent.js", "content.js", "background.js")
    ).lower()
    for site in ("gmail", "yandex", "hh.ru", "python.org", "docs.python.org"):
        assert site not in runtime
