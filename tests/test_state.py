from browser_agent.state import AgentState, MAX_EVIDENCE, MAX_EVIDENCE_CHARS, observation_identity


def _observation(url: str = "https://example.test/page", text: str = "alpha") -> str:
    return f"URL: {url}\nTITLE: Example\n\nVIEWPORT TEXT:\n{text}"


def test_state_accumulates_deduplicates_and_bounds_evidence() -> None:
    state = AgentState(objective="Compare many facts")
    before = state.observe(_observation())
    evidence = {
        "source_url": "https://example.test/page",
        "title": "Fact",
        "query": "alpha",
        "content": "A" * (MAX_EVIDENCE_CHARS + 100),
    }
    state.ingest_result(
        {"tool": "read_page", "arguments": {"query": "alpha"}, "ok": True, "message": "read", "evidence": evidence, "before_fingerprint": before},
        after_observation=_observation(),
    )
    state.ingest_result(
        {"tool": "read_page", "arguments": {"query": "alpha"}, "ok": True, "message": "read", "evidence": evidence, "before_fingerprint": state.current_fingerprint},
        after_observation=_observation(),
    )
    assert len(state.evidence) == 1
    assert len(state.evidence[0].content) == MAX_EVIDENCE_CHARS

    for index in range(MAX_EVIDENCE + 5):
        state.add_evidence({"source_url": f"https://example.test/{index}", "title": str(index), "content": f"unique {index}"})
    assert len(state.evidence) == MAX_EVIDENCE
    assert state.evidence[-1].content == f"unique {MAX_EVIDENCE + 4}"


def test_state_marks_repeated_actions_without_change_as_no_progress() -> None:
    state = AgentState(objective="Find a fact")
    before = state.observe(_observation())
    result = {"tool": "find_text", "arguments": {"text": "alpha"}, "ok": True, "message": "found", "before_fingerprint": before}
    state.ingest_result(result, after_observation=_observation())
    result["before_fingerprint"] = state.current_fingerprint
    state.ingest_result(result, after_observation=_observation())
    assert state.no_progress_count == 2
    assert state.repeated_no_progress_signature() == 'find_text {"text": "alpha"}'
    assert "RECOVERY DIRECTIVE" in state.render()


def test_state_round_trip_is_bounded_and_preserves_verifier_feedback() -> None:
    state = AgentState(objective="Read docs")
    for index in range(30):
        state.observe(_observation(f"https://example.test/{index}", str(index)))
        state.failures.append("failure " + ("x" * 1000))
    state.reject_finish(["Read the target section", "Cite extracted content"], "Not grounded")
    restored = AgentState.from_dict(state.to_dict(), objective="ignored")
    rendered = restored.render(limit=1200)
    assert len(restored.visited_pages) <= 12
    assert len(restored.failures) <= 8
    assert len(rendered) <= 1200
    assert restored.finish_rejections == 1
    assert "Read the target section" in restored.remaining_work


def test_page_fingerprint_includes_viewport_after_large_element_dump() -> None:
    header = "URL: https://example.test\nTITLE: Example\n\nINTERACTIVE ELEMENTS:\n" + ("[e1] name=noise\n" * 1000)
    first = header + "\nVIEWPORT TEXT:\nfirst result"
    second = header + "\nVIEWPORT TEXT:\nsecond result"
    assert observation_identity(first)[2] != observation_identity(second)[2]


def test_state_explicitly_redirects_repeated_read_page_stalls() -> None:
    state = AgentState(objective="Read a specific fact")
    before = state.observe(_observation())
    result = {"tool": "read_page", "arguments": {"query": "alpha"}, "ok": True, "message": "read", "before_fingerprint": before}
    state.ingest_result(result, after_observation=_observation())
    result["before_fingerprint"] = state.current_fingerprint
    state.ingest_result(result, after_observation=_observation())
    rendered = state.render()
    assert "EXTRACTION STALL" in rendered
    assert "Stop re-reading this target" in rendered
