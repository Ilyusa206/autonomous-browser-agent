from browser_agent.observation import PageObservation


def test_observation_render_uses_compact_refs() -> None:
    observation = PageObservation(
        url="https://example.com",
        title="Example Domain",
        text="Example Domain",
        elements=[
            {
                "ref": "e1",
                "tag": "a",
                "role": "",
                "type": "",
                "text": "Learn more",
                "name": "",
                "placeholder": "",
                "href": "https://iana.org/domains/example",
            }
        ],
    )

    rendered = observation.render()

    assert "URL: https://example.com" in rendered
    assert "[e1]" in rendered
    assert "Learn more" in rendered
    assert "<html" not in rendered
