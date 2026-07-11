"""CDP exception-detail formatting contracts."""

from agentcloak.browser._cdp_errors import format_cdp_exception


def test_description_beats_generic_uncaught_text() -> None:
    hint = format_cdp_exception(
        {
            "text": "Uncaught",
            "exception": {
                "description": (
                    "TypeError: Cannot read properties of null (reading 'value')"
                )
            },
            "stackTrace": {
                "callFrames": [
                    {
                        "url": "https://example.test/app.js",
                        "lineNumber": 11,
                        "columnNumber": 6,
                    }
                ]
            },
        }
    )

    assert hint.startswith("TypeError: Cannot read properties of null")
    assert "https://example.test/app.js:12:7" in hint
    assert not hint.startswith("Uncaught")


def test_syntax_error_text_is_preserved() -> None:
    hint = format_cdp_exception(
        {
            "text": "SyntaxError: Unexpected token '}'",
            "url": "https://example.test/page",
            "lineNumber": 0,
            "columnNumber": 14,
        }
    )

    assert hint == (
        "SyntaxError: Unexpected token '}'\n  at https://example.test/page:1:15"
    )


def test_exception_value_is_used_when_description_is_missing() -> None:
    assert (
        format_cdp_exception(
            {"text": "Uncaught", "exception": {"value": "custom failure"}}
        )
        == "custom failure"
    )


def test_diagnostic_is_bounded() -> None:
    hint = format_cdp_exception(
        {"text": "Uncaught", "exception": {"description": "x" * 600}}
    )

    assert len(hint) == 400
    assert hint.endswith(" ... [truncated]")
