import logging
from contextlib import asynccontextmanager
import re

import httpx
import uvicorn
from fastapi import BackgroundTasks, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agent import (
    call_agent, 
    summarize_handback, 
    generate_proactive_message, 
    generate_po_summary,
    parse_intent_json,
    extract_message_text,
    derive_fields_from_intent,
    add_multiple_to_history
)
from config import BACKEND_URL, PORT, OPENAI_API_KEY
from database import (
    close_pool, 
    fetch_po_data, 
    format_po_block, 
    fetch_chat_history, 
    update_thread_state_db,
    ensure_tables,
    fetch_chat_history_by_po,
    insert_po_summary,
    fetch_all_vendor_pos,
    update_po_operational_fields,
    get_pool
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# In-memory session state (keyed by vendor_session_id = vendor_code or po_id)
# ─────────────────────────────────────────────────────────────────────────────

# POs fully resolved this session (escalated or confirmed — never ask again)
_resolved_pos: dict[str, set] = {}

# The PO currently being actively discussed (identified but not yet escalated/confirmed).
# Set when vendor identifies which PO they mean; cleared when that PO is escalated/confirmed.
# Prevents re-triggering disambiguation mid-conversation.
_active_po: dict[str, str] = {}


# ── Resolved PO helpers ───────────────────────────────────────────────────────

def _mark_po_resolved(session_id: str, po_num: str) -> None:
    if not po_num:
        return
    po_num = str(po_num).strip().lstrip("#")
    if session_id not in _resolved_pos:
        _resolved_pos[session_id] = set()
    _resolved_pos[session_id].add(po_num)
    # A resolved PO is no longer active
    if _active_po.get(session_id) == po_num:
        _active_po.pop(session_id, None)


def _get_resolved_pos(session_id: str) -> list[str]:
    return sorted(_resolved_pos.get(session_id, set()))


def _clear_resolved_pos(session_id: str) -> None:
    _resolved_pos.pop(session_id, None)


# ── Active PO helpers ─────────────────────────────────────────────────────────

def _set_active_po(session_id: str, po_num: str) -> None:
    """Mark a specific PO as the one currently being discussed."""
    if not po_num:
        return
    po_num = str(po_num).strip().lstrip("#")
    resolved = _resolved_pos.get(session_id, set())
    if po_num in resolved:
        return  # Already done — don't re-activate
    _active_po[session_id] = po_num
    print(f"TARGET [SESSION] Active PO set to {po_num} for session {session_id}")


def _get_active_po(session_id: str) -> str | None:
    return _active_po.get(session_id)


def _clear_active_po(session_id: str) -> None:
    _active_po.pop(session_id, None)


def _clear_all_session(session_id: str) -> None:
    """Wipe everything for this session (called on chat history reset)."""
    _resolved_pos.pop(session_id, None)
    _active_po.pop(session_id, None)


# ─────────────────────────────────────────────────────────────────────────────
# Multi-PO helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_po_numbers(po_list: list[dict]) -> list[str]:
    seen = set()
    po_numbers = []
    for po in po_list or []:
        po_num = str(po.get("po_num", "")).strip()
        if po_num and po_num not in seen:
            seen.add(po_num)
            po_numbers.append(po_num)
    return po_numbers


def _message_mentions_po(message_text: str, po_numbers: list[str]) -> bool:
    msg = (message_text or "").lower()
    for po in po_numbers:
        po_clean = po.strip()
        if not po_clean:
            continue
        if po_clean.lower() in msg or f"#{po_clean.lower()}" in msg:
            return True
    return False


def _message_matches_unique_item(message_text: str, po_list: list[dict]) -> bool:
    """Returns True only when message clearly matches line items of exactly one PO."""
    msg = (message_text or "").lower().strip()
    if not msg:
        return False

    matched_po_nums = set()
    for po in po_list or []:
        po_num = str(po.get("po_num", "")).strip()
        if not po_num:
            continue

        item_candidates = []
        for li in po.get("line_items", []) or []:
            desc = str(li.get("description", "")).strip().lower()
            if desc:
                item_candidates.append(desc)

        article_desc = str(po.get("article_description", "")).strip().lower()
        if article_desc:
            item_candidates.append(article_desc)

        for candidate in item_candidates:
            if len(candidate) >= 8 and candidate in msg:
                matched_po_nums.add(po_num)
                break

    return len(matched_po_nums) == 1


def _build_disambiguation_prompt(po_numbers: list[str]) -> str:
    if not po_numbers:
        return "Got it — which order are you referring to?"
    if len(po_numbers) == 1:
        return f"Got it — are you referring to order #{po_numbers[0]}?"
    if len(po_numbers) == 2:
        return f"Got it — which order are you referring to, #{po_numbers[0]} or #{po_numbers[1]}?"
    head = ", ".join(f"#{p}" for p in po_numbers[:-1])
    tail = f"#{po_numbers[-1]}"
    return f"Got it — which order are you referring to: {head}, or {tail}?"


def _is_ambiguous_multi_po_message(
    message_text: str,
    po_list: list[dict],
    resolved_pos: list[str],
    active_po: str | None = None
) -> bool:
    """
    Returns True ONLY when all of the following hold:
    1. There is no active PO already being discussed (active_po not set)
    2. More than one PO is still unresolved
    3. Message doesn't mention a PO number
    4. Message doesn't uniquely match one PO's line items

    This prevents the disambiguation from re-firing mid-conversation
    after the vendor has already identified which PO they're discussing.
    """
    # KEY FIX: if an active PO is known, we already know which PO — skip disambiguation entirely
    if active_po:
        print(f"GUARD [GUARD] active_po={active_po} — skipping disambiguation guard")
        return False

    # Filter to only unresolved POs
    unresolved = [p for p in (po_list or []) if str(p.get("po_num", "")).strip().lstrip("#") not in resolved_pos]
    po_numbers = _extract_po_numbers(unresolved)

    if len(po_numbers) <= 1:
        return False
    if _message_mentions_po(message_text, po_numbers):
        return False
    if _message_matches_unique_item(message_text, unresolved):
        return False
    return True


def _get_unresolved_pos(po_list: list[dict], resolved_pos: list[str]) -> list[dict]:
    normalized = set(str(p).strip().lstrip("#") for p in resolved_pos)
    return [p for p in (po_list or []) if str(p.get("po_num", "")).strip().lstrip("#") not in normalized]


def _format_session_context(resolved_pos: list[str], active_po: str | None) -> str:
    lines = []
    if resolved_pos:
        joined = ", ".join(f"#{p}" for p in resolved_pos)
        lines.append(
            f"RESOLVED POs IN THIS SESSION: {joined}\n"
            f"Do NOT ask for clarification about these POs again — they are already resolved."
        )
    if active_po:
        lines.append(
            f"CURRENTLY DISCUSSING: PO #{active_po}\n"
            f"The vendor has already confirmed they are referring to this PO. "
            f"Continue the conversation about PO #{active_po} — do NOT ask which PO again."
        )
    return ("\n\n" + "\n\n".join(lines)) if lines else ""


async def _get_vendor_thread_states(vendor_phone: str, po_id: str) -> dict:
    """
    Fetch thread states for ALL POs belonging to this vendor.
    Bot may only be silenced if ALL vendor POs are human_controlled.
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT po_num, thread_state, bot_context_summary
                FROM selected_open_po_line_items
                WHERE (
                    vendor_phone IS NOT NULL
                    AND regexp_replace(vendor_phone, '\\D', '', 'g') =
                        regexp_replace($1, '\\D', '', 'g')
                )
                OR po_num = $2
                """,
                vendor_phone or "",
                po_id or ""
            )
    except Exception as exc:
        logger.warning("Thread state fetch failed: %s — defaulting to bot_active", exc)
        return {"can_bot_send": True, "human_controlled_pos": [], "bot_context_summary": None}

    if not rows:
        return {"can_bot_send": True, "human_controlled_pos": [], "bot_context_summary": None}

    human_controlled = [r["po_num"] for r in rows if r["thread_state"] == "human_controlled"]
    # Bot only stays silent when EVERY vendor PO is human_controlled
    can_bot_send = len(human_controlled) < len(rows)

    bot_context_summary = None
    for r in rows:
        if r.get("bot_context_summary"):
            bot_context_summary = r["bot_context_summary"]
            break

    return {
        "can_bot_send": can_bot_send,
        "human_controlled_pos": human_controlled,
        "bot_context_summary": bot_context_summary,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Lifespan
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Compass Python Orchestration Service starting up…")
    await ensure_tables()
    logger.info("Database tables verified/created.")
    yield
    logger.info("Shutting down — closing Postgres pool…")
    await close_pool()


app = FastAPI(
    title="Compass Orchestration Service",
    description="Python AI microservice for vendor chat orchestration",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────────────
# Request model
# ─────────────────────────────────────────────────────────────────────────────

class ChatWebhookBody(BaseModel):
    session_id: str
    po_id: str
    supplier_name: str = ""
    vendor_phone: str = ""
    vendor_code: str = ""        # ← vendor-level thread key
    message_text: str = ""
    timestamp: str = ""
    inbound_message_id: str = "" # ← for binding back-update after clarification


# ─────────────────────────────────────────────────────────────────────────────
# Background task — all AI work happens here
# ─────────────────────────────────────────────────────────────────────────────

async def process_chat(body: ChatWebhookBody) -> None:
    session_id     = body.session_id.strip()
    po_id          = body.po_id.strip()
    vendor_phone   = body.vendor_phone.strip()
    vendor_code    = body.vendor_code.strip()
    message_text   = body.message_text.strip()
    inbound_msg_id = body.inbound_message_id.strip()

    print(f"\nAGENT [AGENT] Message: '{message_text}' | PO: {po_id} | vendor_code: {vendor_code or 'N/A'}")

    # ── THREAD STATE GATE (Vendor-Level) ─────────────────────────────────────
    # Bot only stays silent when ALL vendor POs are human_controlled.
    thread_info = await _get_vendor_thread_states(vendor_phone, po_id)
    if not thread_info["can_bot_send"]:
        print(f"🛑 [AGENT] ALL vendor POs are human_controlled — bot silent for {vendor_phone}")
        return

    human_blocked_pos = thread_info.get("human_controlled_pos", [])
    if human_blocked_pos:
        print(f"⚠️ [AGENT] POs {human_blocked_pos} are human_controlled — bot skips those")

    # ── Derive stable vendor session ID ──────────────────────────────────────
    # Priority: vendor_code (from Node) > vendor_code from PO data > po_id fallback
    # This is the key used for both in-memory session state and AI memory
    vendor_session_id = vendor_code or None

    # ── Fetch all PO data for this vendor ────────────────────────────────────
    try:
        po_list = await fetch_all_vendor_pos(vendor_phone, po_id)
        print(f"📊 [AGENT] Fetched {len(po_list)} POs for vendor")
    except Exception as exc:
        logger.error("Postgres fetch failed: %s", exc)
        po_list = []

    # If we didn't get vendor_code from Node, derive it from PO data
    if not vendor_session_id:
        vendor_codes = list(set([p.get("vendor_code") for p in po_list if p.get("vendor_code")]))
        vendor_session_id = "-".join(sorted(vendor_codes)) if vendor_codes else po_id

    print(f"BRAIN [AGENT] Session ID: {vendor_session_id}")

    # Exclude human-controlled POs from AI context
    active_po_list = [p for p in po_list if str(p.get("po_num", "")) not in human_blocked_pos]

    # ── Retrieve session state ────────────────────────────────────────────────
    resolved_pos  = _get_resolved_pos(vendor_session_id)
    current_active_po = _get_active_po(vendor_session_id)
    print(f"📍 [SESSION] resolved={resolved_pos} | active_po={current_active_po}")

    # Override po_id with the active PO for this session if we have one.
    # This prevents the bot from reverting to a default po_id mid-conversation.
    if current_active_po and po_id != current_active_po:
        print(f"SYNC [AGENT] Session active_po={current_active_po} overrides request po_id={po_id}")
        po_id = current_active_po

    unresolved_po_list = _get_unresolved_pos(active_po_list, resolved_pos)

    # ── Deterministic disambiguation guard ───────────────────────────────────
    # Only fires when:
    #   - No active PO is known (current_active_po is None)
    #   - Vendor has multiple unresolved POs
    #   - Message is genuinely ambiguous (no PO# mentioned, no unique item match)
    if _is_ambiguous_multi_po_message(message_text, unresolved_po_list, resolved_pos, current_active_po):
        unresolved_nums = _extract_po_numbers(unresolved_po_list)
        reply_text = _build_disambiguation_prompt(unresolved_nums)
        derived = derive_fields_from_intent("UNCLEAR", "non_perishable")

        await update_po_operational_fields(po_id, {
            "communication_state": derived["communication_state"],
            "risk_level": derived["risk_level"],
            "last_intent": "UNCLEAR",
            "reason": None,
            "ai_paused": False
        })

        payload = {
            "po_id":                 po_id,
            "sender_type":           "bot",
            "message_text":          reply_text,
            "vendor_phone":          vendor_phone,
            "vendor_code":           vendor_session_id,
            "supplier_name":         body.supplier_name,
            "intent":                "UNCLEAR",
            "reason":                "",
            "escalation_required":   False,
            "conversation_complete": False,
            "risk_level":            "low",
            "priority":              "low",
            "sla_due_at":            None,
            "case_type":             None,
            "communication_state":   "awaiting",
            "extracted_eta":         None,
            "shortage_note":         None,
            "ai_paused":             False,
            "vendor_initiated":      False,
            "confidence_score":      0.95,
            "linked_pos":            [],
            "bound_po_num":          None,
            "po_binding_source":     "unresolved",
            "po_binding_confidence": 0.00,
        }
        print(f"COMPASS [AGENT] Disambiguation sent for session {vendor_session_id}: {reply_text}")
        
        # ── Update AI Memory with the guarded turn ───────────────────────────
        # This ensures the AI has context of the user's message and our 
        # disambiguation question in the next turn.
        await add_multiple_to_history(vendor_session_id, [
            {"role": "user", "content": message_text},
            {"role": "assistant", "content": reply_text}
        ])

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                await client.post(f"{BACKEND_URL}/api/chat-message", json=payload)
            except Exception as exc:
                print(f"❌ [AGENT] Disambiguation POST failed: {exc}")
        return  # Do NOT call AI — wait for vendor to identify which PO

    # ── Build AI context ──────────────────────────────────────────────────────
    po_data_block = format_po_block(active_po_list)
    session_context = _format_session_context(resolved_pos, current_active_po)

    # Append handback summary if a human had previously taken over
    if thread_info.get("bot_context_summary"):
        session_context += (
            f"\n\nCONTEXT FROM PREVIOUS HUMAN CONVERSATION:\n"
            f"{thread_info['bot_context_summary']}\n"
            f"Continue naturally — do not re-ask questions already answered."
        )

    # ── Call OpenAI ───────────────────────────────────────────────────────────
    print("BOT [AGENT] Calling OpenAI...")
    try:
        ai_output = await call_agent(
            vendor_session_id,
            message_text,
            po_data_block + session_context
        )
        print(f"CHECK [AGENT] OpenAI Response received ({len(ai_output)} chars)")
    except Exception as exc:
        logger.error("OpenAI call failed: %s", exc)
        return

    # ── Parse AI output ───────────────────────────────────────────────────────
    print(f"RAW [AGENT] OpenAI Raw Output:\n{ai_output}\n")
    intent_data = parse_intent_json(ai_output)
    reply_text  = extract_message_text(ai_output)
    
    print(f"DEBUG [AGENT] Parsed Intent Data: {intent_data}")

    extracted_po   = intent_data.get("po_num")
    po_num_from_ai = str(extracted_po).replace("#", "").strip() if extracted_po else po_id
    intent         = intent_data.get("intent", "UNCLEAR")

    # Extract po_binding from new schema
    po_binding           = intent_data.get("po_binding") or {}
    ai_bound_po          = str(po_binding.get("po_number", "") or "").replace("#", "").strip() or po_num_from_ai
    ai_binding_confidence = float(po_binding.get("binding_confidence", 0.0) or 0.0)
    ai_binding_source    = str(po_binding.get("binding_source", "inferred") or "inferred")
    requires_clarif      = bool(po_binding.get("requires_clarification", False))
    clarif_question      = po_binding.get("clarification_question") or None

    # ── Update active_po from AI output ──────────────────────────────────────
    # Only lock a PO as active if the AI has resolved it with confidence
    # and explicitly says NO clarification is required.
    
    final_confirmed_po = None
    if not requires_clarif:
        # Priority: po_binding (JSON) > po_num (JSON)
        final_confirmed_po = (
            str(po_binding.get("po_number", "") or "").replace("#", "").strip() or
            str(intent_data.get("po_num", "") or "").replace("#", "").strip()
        )
    
    # ── FALLBACK: Trust regex if user explicitly mentions a valid PO ────────
    # If the user mentioned a PO number but the AI is still "UNCLEAR", 
    # we trust the number provided in the message text.
    if not final_confirmed_po or intent == "UNCLEAR":
        unresolved_nums = _extract_po_numbers(unresolved_po_list)
        for num in unresolved_nums:
            if num in message_text or f"#{num}" in message_text:
                print(f"TRUST [AGENT] User explicitly mentioned PO {num} — overriding UNCLEAR intent")
                final_confirmed_po = num
                intent = "INFO_QUERY" # Reset from UNCLEAR to general query
                break

    # Final safety check: if it's the generic fallback po_id, 
    # only accept it if AI was actually confident.
    if final_confirmed_po == po_id and ai_binding_source == "unresolved":
        final_confirmed_po = None

    if final_confirmed_po:
        _set_active_po(vendor_session_id, final_confirmed_po)
        ai_bound_po = final_confirmed_po
        po_num_from_ai = final_confirmed_po
    else:
        # If unresolved or unclear, DO NOT lock an active PO 
        # and DO NOT bind to a specific PO in the DB yet.
        ai_bound_po = None
        # We still use the request's po_id for operational field updates 
        # (communication_state=awaiting) but we won't claim the message belongs to it.
        po_num_from_ai = po_id
    
    print(f"LOC [SESSION] Final derived PO for this turn: {final_confirmed_po or 'NONE (unresolved)'}")

    derived = derive_fields_from_intent(intent, "non_perishable")

    # ── Handle AI clarification request ──────────────────────────────────────
    # Edge case: AI itself decides clarification is needed (beyond our guard)
    if requires_clarif and clarif_question and intent == "UNCLEAR":
        print(f"❓ [AGENT] AI requests clarification: {clarif_question}")
        clarif_payload = {
            "po_id":                 po_id,
            "sender_type":           "bot",
            "message_text":          clarif_question,
            "vendor_phone":          vendor_phone,
            "vendor_code":           vendor_session_id,
            "supplier_name":         body.supplier_name,
            "intent":                "UNCLEAR",
            "reason":                "",
            "escalation_required":   False,
            "conversation_complete": False,
            "risk_level":            "low",
            "priority":              "low",
            "sla_due_at":            None,
            "case_type":             None,
            "communication_state":   "awaiting",
            "extracted_eta":         None,
            "shortage_note":         None,
            "ai_paused":             False,
            "vendor_initiated":      False,
            "confidence_score":      0.95,
            "linked_pos":            [],
            "bound_po_num":          None,
            "po_binding_source":     "unresolved",
            "po_binding_confidence": 0.00,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                await client.post(f"{BACKEND_URL}/api/chat-message", json=clarif_payload)
            except Exception as exc:
                print(f"❌ [AGENT] Clarification POST failed: {exc}")
        return

    # ── Back-update inbound message binding ──────────────────────────────────
    # Now that the AI has confirmed which PO, patch the already-saved vendor message
    if inbound_msg_id and ai_bound_po and ai_binding_source != "unresolved" and ai_bound_po != po_id:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.patch(
                    f"{BACKEND_URL}/api/message/{inbound_msg_id}/po-binding",
                    json={
                        "bound_po_num":          ai_bound_po,
                        "po_binding_confidence": ai_binding_confidence or 0.90,
                        "po_binding_source":     ai_binding_source,
                    }
                )
                print(f"LINK [AGENT] Binding back-updated: msg {inbound_msg_id} -> {ai_bound_po}")
        except Exception as exc:
            logger.warning("Binding back-update failed: %s", exc)

    # ── Track resolved POs ────────────────────────────────────────────────────
    # When escalation fires for a specific PO → mark it resolved, clear active
    if intent_data.get("escalate") and ai_bound_po and intent != "UNCLEAR":
        _mark_po_resolved(vendor_session_id, ai_bound_po)
        print(f"✅ [SESSION] PO {ai_bound_po} resolved → resolved_set={_get_resolved_pos(vendor_session_id)}")

    # Also honour linked_pos status updates
    for lp in intent_data.get("linked_pos", []) or []:
        lp_num    = str(lp.get("po_num", "")).strip()
        lp_status = str(lp.get("status", "")).lower()
        if lp_num and lp_status in ("confirmed", "resolved", "escalated"):
            _mark_po_resolved(vendor_session_id, lp_num)

    # ── AI Auto-Pause (price/payment handoff) ─────────────────────────────────
    if intent_data.get("ai_paused"):
        await update_thread_state_db(po_num_from_ai, "human_controlled")
        logger.info("AI Auto-Paused | po=%s | reason=%s", po_num_from_ai, intent)

    # ── Sync operational fields to DB ─────────────────────────────────────────
    def _s(v):
        return None if isinstance(v, str) and not v.strip() else v

    await update_po_operational_fields(po_num_from_ai, {
        "communication_state": _s(derived["communication_state"]),
        "risk_level":          _s(derived["risk_level"]),
        "last_intent":         _s(intent),
        "reason":              _s(intent_data.get("reason")),
        "ai_paused":           intent_data.get("ai_paused", False),
    })

    # ── POST bot reply to Node backend ────────────────────────────────────────
    payload = {
        "po_id":                 po_num_from_ai,
        "sender_type":           "bot",
        "sender_label":          "Compass Bot",
        "message_text":          reply_text,
        "vendor_phone":          vendor_phone,
        "vendor_code":           vendor_session_id,
        "supplier_name":         body.supplier_name,
        "intent":                intent,
        "reason":                intent_data.get("reason", ""),
        "escalation_required":   intent_data.get("escalate", False),
        "conversation_complete": intent_data.get("conversation_complete", False),
        "risk_level":            derived["risk_level"],
        "priority":              derived["priority"],
        "sla_due_at":            derived["sla_due_at"],
        "case_type":             derived["case_type"],
        "communication_state":   derived["communication_state"],
        "extracted_eta":         intent_data.get("extracted_eta") or None,
        "shortage_note":         intent_data.get("shortage_note"),
        "ai_paused":             intent_data.get("ai_paused", False),
        "vendor_initiated":      intent_data.get("vendor_initiated", False),
        "confidence_score":      intent_data.get("confidence_score", 0.0),
        "linked_pos":            intent_data.get("linked_pos", []),
        # PO binding — used by Node for case creation targeting
        "bound_po_num":          ai_bound_po if ai_binding_source != "unresolved" else None,
        "po_binding_source":     ai_binding_source,
        "po_binding_confidence": ai_binding_confidence,
    }

    print(f"📤 [AGENT] Posting bot reply → po={po_num_from_ai} | intent={intent} | escalate={intent_data.get('escalate')} | bound_po={ai_bound_po}")
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(f"{BACKEND_URL}/api/chat-message", json=payload)
            print(f"🏁 [AGENT] Backend POST: {resp.status_code}")
        except Exception as exc:
            logger.error("Backend POST failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/webhook/chat", status_code=200)
async def webhook_chat(body: ChatWebhookBody, background_tasks: BackgroundTasks):
    """Receive a vendor message and return 200 immediately. AI runs in background."""
    background_tasks.add_task(process_chat, body)
    return {"status": "accepted"}


class ClearSessionBody(BaseModel):
    session_id: str = ""
    vendor_code: str = ""


@app.post("/webhook/clear-session", status_code=200)
async def webhook_clear_session(body: ClearSessionBody):
    """Clear all in-memory session state when operator resets chat history."""
    from agent import _memory, _memory_lock
    sid = body.session_id.strip() or body.vendor_code.strip()
    if sid:
        async with _memory_lock:
            _memory.pop(sid, None)
        _clear_all_session(sid)
        logger.info("Session cleared for: %s", sid)
    return {"status": "cleared", "session_id": sid}


@app.get("/health", status_code=200)
async def health():
    return {"status": "ok"}


@app.post("/api/summary/{po_num}")
async def post_summary(po_num: str):
    from fastapi import HTTPException
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OpenAI API key not configured.")
    messages = await fetch_chat_history_by_po(po_num)
    if not messages:
        raise HTTPException(status_code=404, detail=f"No chat history found for PO {po_num}.")
    try:
        result = await generate_po_summary(po_num, messages)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"OpenAI call failed: {exc}")
    try:
        stored = await insert_po_summary(
            po_num=po_num,
            summary_text=result["summary_text"],
            key_intent=result["key_intent"],
            risk_level=result["risk_level"],
            message_count=len(messages),
            model_used=result["model_used"],
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to store summary: {exc}")
    return {
        "po_num":        stored["po_num"],
        "summary":       stored["summary_text"],
        "key_intent":    stored["key_intent"],
        "risk_level":    stored["risk_level"],
        "message_count": stored["message_count"],
        "generated_at":  stored["generated_at"].isoformat() if stored["generated_at"] else None,
    }


class HandbackBody(BaseModel):
    po_id: str


@app.post("/webhook/handback")
async def webhook_handback(body: HandbackBody, background_tasks: BackgroundTasks):
    po_id = body.po_id
    async def process_handback():
        history = await fetch_chat_history(po_id)
        summary = await summarize_handback(history)
        await update_thread_state_db(po_id, "bot_active", bot_context_summary=summary)
        logger.info(f"Handback done | po={po_id}")
    background_tasks.add_task(process_handback)
    return {"status": "accepted"}


class ProactiveUpdateBody(BaseModel):
    po_id: str
    supplier_name: str
    vendor_phone: str
    changes: list[str]


@app.post("/webhook/proactive-update")
async def webhook_proactive_update(body: ProactiveUpdateBody, background_tasks: BackgroundTasks):
    po_id        = body.po_id
    vendor_phone = body.vendor_phone
    supplier_name = body.supplier_name
    changes      = body.changes

    async def process_proactive():
        message_text = await generate_proactive_message(po_id, changes)
        payload = {
            "po_id":         po_id,
            "sender_type":   "bot",
            "message_text":  message_text,
            "vendor_phone":  vendor_phone,
            "supplier_name": supplier_name,
            "intent":        "PROACTIVE_UPDATE",
            "escalation_required": False,
            "conversation_complete": False,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                await client.post(f"{BACKEND_URL}/api/chat-message", json=payload)
            except Exception as exc:
                logger.error(f"Proactive POST failed: {exc}")

    background_tasks.add_task(process_proactive)
    return {"status": "accepted"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
