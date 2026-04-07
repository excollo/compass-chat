import re
import json
from typing import Any, Dict, Tuple

ESCALATE_INTENTS = [
    "PARTIAL",
    "REJECTED",
    "DELAYED",
    "PRICE_UPDATE",
    "QUANTITY_CHANGE",
    "PO_CANCELLATION",
    "PAYMENT_ISSUE",
    "QUALITY_ISSUE",
]

PRIORITY_MAP: Dict[str, str] = {
    "PO_CANCELLATION": "🔴 CRITICAL",
    "REJECTED":        "🔴 HIGH",
    "PRICE_UPDATE":    "🟠 HIGH",
    "PAYMENT_ISSUE":   "🟠 HIGH",
    "PARTIAL":         "🟡 MEDIUM",
    "DELAYED":         "🟡 MEDIUM",
    "QUANTITY_CHANGE": "🟡 MEDIUM",
    "QUALITY_ISSUE":   "🟡 MEDIUM",
}

INTENT_LABELS: Dict[str, str] = {
    "PARTIAL":         "Partial Delivery",
    "REJECTED":        "Rejection",
    "DELAYED":         "Delivery Delay",
    "PRICE_UPDATE":    "Price Update Request",
    "QUANTITY_CHANGE": "Quantity Change Request",
    "PO_CANCELLATION": "PO Cancellation Request",
    "PAYMENT_ISSUE":   "Payment Query",
    "QUALITY_ISSUE":   "Quality / Spec Issue",
}


def _extract_intent_json(ai_output: str) -> Dict[str, Any]:
    """Extract and parse the INTENT_JSON block from AI output."""
    match = re.search(r'INTENT_JSON:\s*(\{[\s\S]*?\})', ai_output)
    try:
        intent_data: Dict[str, Any] = json.loads(match.group(1)) if match else {}
    except Exception:
        intent_data = {}

    # Apply defaults if keys are missing
    intent_data.setdefault("intent", "UNCLEAR")
    intent_data.setdefault("po_num", "")
    intent_data.setdefault("vendor_name", "")
    intent_data.setdefault("reason", "")
    intent_data.setdefault("escalate", False)
    intent_data.setdefault("conversation_complete", False)

    return intent_data


def _clean_reply_text(ai_output: str) -> str:
    """Strip the INTENT_JSON block from the AI output to get the clean reply."""
    return re.sub(r'INTENT_JSON:[\s\S]*$', '', ai_output).strip()


def _build_admin_message(
    should_escalate: bool,
    intent_data: Dict[str, Any],
    po_num: str,
    message_text: str,
) -> str:
    """Build the admin escalation message when should_escalate is True."""
    if not should_escalate:
        return ""

    intent = intent_data["intent"]
    priority = PRIORITY_MAP.get(intent, "🟡")
    label = INTENT_LABELS.get(intent, intent)
    reason = intent_data.get("reason") or "See vendor message"

    return (
        f"{priority} *PO Exception — Action Required*\n\n"
        f"*PO Number:* {po_num}\n"
        f"*Vendor:* {intent_data.get('vendor_name', '')}\n"
        f"*Issue Type:* {label}\n"
        f"*Vendor Said:* \"{message_text}\"\n"
        f"*Details:* {reason}\n\n"
        f"Please review and contact vendor."
    )


def parse_intent(
    ai_output: str,
    po_id: str,
    message_text: str,
) -> Tuple[str, Dict[str, Any], bool, str]:
    """
    Full intent parsing pipeline.

    Returns:
        reply_text      — clean bot reply (no INTENT_JSON)
        intent_data     — parsed intent dict with defaults applied
        should_escalate — whether to escalate this conversation
        admin_message   — formatted alert string (empty if no escalation)
    """
    intent_data = _extract_intent_json(ai_output)
    reply_text = _clean_reply_text(ai_output)

    # po_num fallback — never send blank po_num to backend
    po_num = intent_data.get("po_num") or po_id
    intent_data["po_num"] = po_num

    should_escalate: bool = (
        intent_data.get("escalate") is True
        and intent_data.get("intent") in ESCALATE_INTENTS
        and intent_data.get("intent") != "INFO_QUERY"
    )

    admin_message = _build_admin_message(should_escalate, intent_data, po_num, message_text)

    return reply_text, intent_data, should_escalate, admin_message
