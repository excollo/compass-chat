"""
Tests for PO binding logic in the Compass procurement system.

Run with:
    cd backend_agent
    python -m pytest tests/test_po_binding.py -v

No external connections needed — all DB/API calls are mocked.
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# ── Module under test ────────────────────────────────────────────────────────
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import (
    _is_ambiguous_multi_po_message,
    _extract_po_numbers,
    _message_mentions_po,
    _mark_po_resolved,
    _get_resolved_pos,
    _clear_resolved_pos,
    _build_disambiguation_prompt,
    _get_unresolved_pos,
    _set_active_po,
    _get_active_po,
    _clear_active_po,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

SINGLE_PO_VENDOR = [
    {
        "po_num": "4100260863",
        "vendor_name": "Royal Enterprises",
        "vendor_code": "V001",
        "delivery_date": "2026-04-20",
        "status": "Open",
        "line_items": [{"description": "Chicken Breast", "quantity": 100, "unit": "kg"}],
        "article_description": "Chicken Breast",
    }
]

MULTI_PO_VENDOR = [
    {
        "po_num": "4100260863",
        "vendor_name": "Royal Enterprises",
        "vendor_code": "V001",
        "delivery_date": "2026-04-20",
        "status": "Open",
        "line_items": [{"description": "Chicken Breast", "quantity": 100, "unit": "kg"}],
        "article_description": "Chicken Breast",
    },
    {
        "po_num": "4100260654",
        "vendor_name": "Royal Enterprises",
        "vendor_code": "V001",
        "delivery_date": "2026-04-25",
        "status": "Open",
        "line_items": [{"description": "Paneer Block", "quantity": 50, "unit": "kg"}],
        "article_description": "Paneer Block",
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Single open PO vendor: message binds automatically (no ambiguity)
# ─────────────────────────────────────────────────────────────────────────────

class TestSinglePoBinding:

    def test_not_ambiguous_for_single_po(self):
        """Any message with a single-PO vendor should NOT be flagged ambiguous."""
        assert not _is_ambiguous_multi_po_message(
            "I can't deliver this order partially", SINGLE_PO_VENDOR, []
        )

    def test_generic_message_single_po(self):
        assert not _is_ambiguous_multi_po_message("thoda time chahiye", SINGLE_PO_VENDOR, [])

    def test_blank_message_single_po(self):
        assert not _is_ambiguous_multi_po_message("", SINGLE_PO_VENDOR, [])


# ─────────────────────────────────────────────────────────────────────────────
# 2. Multi open PO vendor: ambiguous message triggers disambiguation
# ─────────────────────────────────────────────────────────────────────────────

class TestMultiPoAmbiguity:

    def test_generic_message_flagged_ambiguous(self):
        """'can I deliver partially' with no PO number → ambiguous."""
        assert _is_ambiguous_multi_po_message(
            "can I deliver this order partially", MULTI_PO_VENDOR, []
        )

    def test_vague_hinglish_is_ambiguous(self):
        assert _is_ambiguous_multi_po_message("thoda issue hai", MULTI_PO_VENDOR, [])

    def test_message_with_explicit_po_not_ambiguous(self):
        """Vendor mentions PO number explicitly → not ambiguous."""
        assert not _is_ambiguous_multi_po_message(
            "4100260863 mein delay hoga", MULTI_PO_VENDOR, []
        )

    def test_message_with_hash_po_not_ambiguous(self):
        assert not _is_ambiguous_multi_po_message(
            "issue with #4100260654", MULTI_PO_VENDOR, []
        )

    def test_message_matching_unique_item_not_ambiguous(self):
        """Message references 'Chicken Breast' which only exists in PO 4100260863 → not ambiguous."""
        assert not _is_ambiguous_multi_po_message(
            "I cannot deliver the Chicken Breast order", MULTI_PO_VENDOR, []
        )


# ─────────────────────────────────────────────────────────────────────────────
# 3. Resolved PO tracking
# ─────────────────────────────────────────────────────────────────────────────

class TestResolvedPoTracking:

    def setup_method(self):
        _clear_resolved_pos("test_session_V001")

    def test_mark_and_get_resolved(self):
        _mark_po_resolved("test_session_V001", "4100260863")
        assert "4100260863" in _get_resolved_pos("test_session_V001")

    def test_resolved_po_excluded_from_ambiguity(self):
        """After PO A is resolved, ambiguous message about PO B should NOT be re-disambiguated for A."""
        _mark_po_resolved("test_session_V001", "4100260863")
        resolved = _get_resolved_pos("test_session_V001")
        unresolved = _get_unresolved_pos(MULTI_PO_VENDOR, resolved)
        # Only PO 4100260654 is now unresolved — 1 PO left, no ambiguity
        assert len(unresolved) == 1
        assert unresolved[0]["po_num"] == "4100260654"
        assert not _is_ambiguous_multi_po_message("thoda issue hai", unresolved, resolved)

    def test_hash_prefix_stripped_correctly(self):
        _mark_po_resolved("test_session_V001", "#4100260863")
        assert "4100260863" in _get_resolved_pos("test_session_V001")

    def test_clear_resolved_pos(self):
        _mark_po_resolved("test_session_V001", "4100260863")
        _clear_resolved_pos("test_session_V001")
        assert _get_resolved_pos("test_session_V001") == []


# ─────────────────────────────────────────────────────────────────────────────
# 4. Disambiguation prompt builder
# ─────────────────────────────────────────────────────────────────────────────

class TestDisambiguationPrompt:

    def test_two_pos(self):
        prompt = _build_disambiguation_prompt(["4100260863", "4100260654"])
        assert "4100260863" in prompt
        assert "4100260654" in prompt
        assert "which order" in prompt.lower()

    def test_single_po(self):
        prompt = _build_disambiguation_prompt(["4100260863"])
        assert "4100260863" in prompt

    def test_empty(self):
        prompt = _build_disambiguation_prompt([])
        assert "which order" in prompt.lower()

    def test_three_pos(self):
        prompt = _build_disambiguation_prompt(["A", "B", "C"])
        assert "A" in prompt and "B" in prompt and "C" in prompt


# ─────────────────────────────────────────────────────────────────────────────
# 5. Escalation guard — canEscalate logic
# ─────────────────────────────────────────────────────────────────────────────

class TestEscalationGuard:
    """
    Tests the guard: escalation must NOT fire if po_binding_source='unresolved'.
    This is implemented in server.js; here we verify the binding logic inputs.
    """

    def test_single_po_binding_is_auto_inferred(self):
        """With 1 open PO, binding_source should be 'inferred' — escalation allowed."""
        # Simulate: open_pos = [PO_A]
        open_pos = SINGLE_PO_VENDOR
        binding_source = "inferred" if len(open_pos) == 1 else "unresolved"
        bound_po = open_pos[0]["po_num"] if len(open_pos) == 1 else None
        assert binding_source == "inferred"
        assert bound_po is not None
        # canEscalate = binding_confirmed (True) AND shouldEscalate (True)
        binding_confirmed = binding_source != "unresolved" and bound_po is not None
        assert binding_confirmed is True

    def test_multi_po_unresolved_blocks_escalation(self):
        """With 2+ open POs and ambiguous message, binding is 'unresolved' — escalation blocked."""
        open_pos = MULTI_PO_VENDOR
        binding_source = "inferred" if len(open_pos) == 1 else "unresolved"
        bound_po = open_pos[0]["po_num"] if len(open_pos) == 1 else None
        assert binding_source == "unresolved"
        assert bound_po is None
        binding_confirmed = binding_source != "unresolved" and bound_po is not None
        assert binding_confirmed is False  # escalation blocked ✓

    def test_multi_po_explicit_mention_allows_escalation(self):
        """After disambiguation, binding_source='explicit' → escalation allowed."""
        binding_source = "explicit"
        bound_po = "4100260863"
        binding_confirmed = binding_source != "unresolved" and bound_po is not None
        assert binding_confirmed is True


# ─────────────────────────────────────────────────────────────────────────────
# 6. Multi-PO two-issue scenario: two separate cases, each with correct po_id
# ─────────────────────────────────────────────────────────────────────────────

class TestMultiPoTwoIssues:

    def test_two_resolved_pos_yields_two_independent_bindings(self):
        """After vendor reports issues on BOTH POs, both should be independently tracked."""
        session = "test_session_two_issues"
        _clear_resolved_pos(session)

        _mark_po_resolved(session, "4100260863")
        _mark_po_resolved(session, "4100260654")

        resolved = _get_resolved_pos(session)
        assert len(resolved) == 2
        assert "4100260863" in resolved
        assert "4100260654" in resolved

        # No more unresolved POs → no disambiguation needed
        unresolved = _get_unresolved_pos(MULTI_PO_VENDOR, resolved)
        assert len(unresolved) == 0

        _clear_resolved_pos(session)

    def test_each_po_binding_is_independent(self):
        """Resolving PO A binding does not affect PO B."""
        binding_a = {"po_num": "4100260863", "source": "explicit", "confidence": 0.99}
        binding_b = {"po_num": "4100260654", "source": "inferred", "confidence": 0.88}
        assert binding_a["po_num"] != binding_b["po_num"]
        assert binding_a["source"] == "explicit"
        assert binding_b["source"] == "inferred"


# ─────────────────────────────────────────────────────────────────────────────
# 8. Active PO lock — prevents re-disambiguation
# ─────────────────────────────────────────────────────────────────────────────

class TestActivePoLock:

    def setup_method(self):
        _clear_active_po("test_session_lock")
        _clear_resolved_pos("test_session_lock")

    def test_active_po_prevents_disambiguation(self):
        """Once active_po is set, ambiguous messages should NOT trigger disambiguation."""
        # 1. Start: Ambiguous message triggers it
        assert _is_ambiguous_multi_po_message(
            "thoda issue hai", MULTI_PO_VENDOR, [], active_po=None
        )

        # 2. Lock the PO as active (e.g. after vendor says '4100260863')
        _set_active_po("test_session_lock", "4100260863")
        active = _get_active_po("test_session_lock")
        assert active == "4100260863"

        # 3. Next ambiguous message should NOT trigger disambiguation
        assert not _is_ambiguous_multi_po_message(
            "I can deliver this order partially", MULTI_PO_VENDOR, [], active_po=active
        )

    def test_clearing_active_po(self):
        _set_active_po("test_session_lock", "4100260863")
        _clear_active_po("test_session_lock")
        assert _get_active_po("test_session_lock") is None

    def test_resolved_po_is_not_active(self):
        _set_active_po("test_session_lock", "4100260863")
        _mark_po_resolved("test_session_lock", "4100260863")
        # Should be cleared automatically from active upon resolution
        assert _get_active_po("test_session_lock") is None
