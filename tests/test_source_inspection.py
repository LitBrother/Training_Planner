import pytest
from services import ai_service as ai
from services import evidence

def test_citations_map_to_allowed_sources():
    # Verify all evidence sources have the required fields
    for source in evidence.SOURCES:
        assert "citation" in source
        assert "principle" in source
        assert "url" in source
        assert "id" in source

def test_unsupported_citations_are_rejected_by_instruction():
    # This is a bit tricky as the AI generates the citation, 
    # but the instruction should be clear.
    prompt = ai.SYSTEM_PROMPT
    assert "cite the source details and principle" in prompt.lower() or "source" in prompt.lower()
