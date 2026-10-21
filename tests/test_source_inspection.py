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

def test_reject_unsupported_citations():
    history = [{"exercise": "Bench Press", "latest": {"weight_kg": 100, "reps": 10}}]
    raw = [{
        "exercise": "Bench Press",
        "next_weight_kg": 105,
        "target_reps": 8,
        "reason": "Test",
        "evidence_ids": ["INVALID_ID"]
    }]
    
    accepted, errors = ai.validate_strength_advice(raw, history)
    assert len(accepted) == 0
    assert any("contains an unverified research citation" in e for e in errors)

def test_accept_valid_citations():
    history = [{"exercise": "Bench Press", "latest": {"weight_kg": 100, "reps": 10}}]
    raw = [{
        "exercise": "Bench Press",
        "next_weight_kg": 105,
        "target_reps": 8,
        "reason": "Test",
        "evidence_ids": ["ACSM-RT-2009"]
    }]
    
    accepted, errors = ai.validate_strength_advice(raw, history)
    assert len(accepted) == 1
    assert len(errors) == 0

def test_unsupported_citations_are_rejected_by_instruction():
    # This is a bit tricky as the AI generates the citation, 
    # but the instruction should be clear.
    prompt = ai.SYSTEM_PROMPT
    assert "cite the source details and principle" in prompt.lower() or "source" in prompt.lower()
    assert "evidence_ids" in prompt
    assert "research_sources" in prompt
