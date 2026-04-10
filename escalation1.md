PROMPT 1 — compass_chat/backend/server.js
Files to paste into Antigravity context

backend/server.js
backend/package.json


Make the following changes to server.js only.
Do not change database.js. Do not change any existing endpoints.
Do not touch the .env file.

Change 1 — Add Supabase client at the top of server.js
After the existing require statements at the top, add:
jsconst { createClient } = require('@supabase/supabase-js');

const supabase = createClient(
  process.env.SUPABASE_URL,
  process.env.SUPABASE_SERVICE_KEY
);

Change 2 — Add intent to escalation_reason mapping
Add this constant after the supabase client setup:
js// Maps bot intent values to escalations table reason values
const INTENT_TO_REASON = {
  'PARTIAL':         'partial_delivery',
  'REJECTED':        'order_rejected',
  'DELAYED':         'delivery_delay',
  'PRICE_UPDATE':    'pricing_issue',
  'QUANTITY_CHANGE': 'partial_delivery',
  'PO_CANCELLATION': 'order_rejected',
  'PAYMENT_ISSUE':   'payment_issue',
  'QUALITY_ISSUE':   'quality_issue',
};

const INTENT_TO_PRIORITY = {
  'PO_CANCELLATION': 'critical',
  'REJECTED':        'critical',
  'PRICE_UPDATE':    'high',
  'PAYMENT_ISSUE':   'high',
  'PARTIAL':         'high',
  'DELAYED':         'medium',
  'QUANTITY_CHANGE': 'medium',
  'QUALITY_ISSUE':   'medium',
};

const INTENT_TO_CATEGORY = {
  'PARTIAL':         'Shortage',
  'REJECTED':        'Operation',
  'DELAYED':         'Delay',
  'PRICE_UPDATE':    'Pricing',
  'QUANTITY_CHANGE': 'Shortage',
  'PO_CANCELLATION': 'Operation',
  'PAYMENT_ISSUE':   'Payment',
  'QUALITY_ISSUE':   'Quality',
};

Change 3 — Add createEscalation helper function
Add this async function after the constants above:
jsasync function createEscalationInSupabase(poData) {
  try {
    const {
      po_id,
      vendor_phone,
      supplier_name,
      intent,
      reason,
      admin_message,
      sent_at
    } = poData;

    // fetch PO details from selected_open_po_line_items
    const { data: poRecord } = await supabase
      .from('selected_open_po_line_items')
      .select('*')
      .eq('po_num', po_id)
      .single();

    if (!poRecord) {
      console.error(`❌ [ESCALATION] PO not found in Supabase: ${po_id}`);
      return null;
    }

    const escalationReason = INTENT_TO_REASON[intent] || 'other';
    const priority = INTENT_TO_PRIORITY[intent] || 'medium';
    const category = INTENT_TO_CATEGORY[intent] || 'Operation';

    const escalationData = {
      po_num:               po_id,
      vendor_code:          poRecord.vendor_code || '',
      vendor_name:          poRecord.vendor_name || supplier_name || '',
      vendor_phone:         vendor_phone || poRecord.vendor_phone || '',
      delivery_site:        poRecord.unit_description || poRecord.unit || '',
      escalation_reason:    escalationReason,
      reason_detail:        reason || 'Escalated by bot',
      category:             category,
      priority:             priority,
      status:               'open',
      po_status:            'open',
      delivery_date:        poRecord.delivery_date || new Date().toISOString().split('T')[0],
      document_date:        poRecord.po_date || null,
      total_lines:          1,
      pending_lines:        1,
      fulfillment_rate:     0,
      vendor_sla_applies:   intent === 'NO_RESPONSE' ? true : false,
      vendor_sla_hours:     24,
      last_bot_message_at:  sent_at || new Date().toISOString(),
      bot_attempt_count:    1,
      operator_sla_hours:   2,
      escalation_created_at: new Date().toISOString(),
      ai_summary:           admin_message || `Bot escalated PO ${po_id} — ${reason}`,
    };

    const { data: inserted, error } = await supabase
      .from('escalations')
      .insert(escalationData)
      .select()
      .single();

    if (error) {
      console.error(`❌ [ESCALATION] Supabase insert failed:`, error.message);
      return null;
    }

    console.log(`✅ [ESCALATION] Created escalation for PO ${po_id} — reason: ${escalationReason} — id: ${inserted.id}`);
    return inserted;

  } catch (err) {
    console.error(`❌ [ESCALATION] createEscalation failed:`, err.message);
    return null;
  }
}

Change 4 — Trigger escalation creation in /api/chat-message
In the existing /api/chat-message POST handler, find where the message
is saved and broadcast. After the broadcast() call, add escalation creation:
Find this existing block:
js// Broadcast via WebSocket
broadcast({ 
  event: 'new_message', 
  po_id, 
  sender_type, 
  message_text, 
  ...
});
After that broadcast block, add:
js// If bot flagged escalation — create record in Supabase escalations table
if (escalate === true && sender_type === 'bot' && intent) {
  console.log(`🚨 [ESCALATION] Bot flagged escalation for PO ${po_id} — intent: ${intent}`);
  
  const escalationRecord = await createEscalationInSupabase({
    po_id,
    vendor_phone,
    supplier_name,
    intent,
    reason: req.body.reason || '',
    admin_message,
    sent_at: saved.sent_at
  });

  // broadcast escalation event separately so admin frontend
  // can update notification bell in real time
  if (escalationRecord) {
    broadcast({
      event: 'new_escalation',
      escalation_id:  escalationRecord.id,
      po_num:         po_id,
      vendor_name:    escalationRecord.vendor_name,
      reason:         escalationRecord.escalation_reason,
      reason_detail:  escalationRecord.reason_detail,
      priority:       escalationRecord.priority,
      category:       escalationRecord.category,
      ai_summary:     escalationRecord.ai_summary,
      created_at:     escalationRecord.escalation_created_at
    });
  }

  // also update thread_state to escalated
  await supabase
    .from('selected_open_po_line_items')
    .update({ thread_state: 'escalated' })
    .eq('po_num', po_id);
}

Change 5 — Add supabase package to package.json
Add to dependencies in package.json:
json"@supabase/supabase-js": "^2.0.0"
Then run:
bashnpm install @supabase/supabase-js

Do NOT change

WebSocket broadcast logic
saveMessage function
Any existing endpoints
database.js
.env file


How to test
bash# 1. Restart server.js after npm install
node server.js

# 2. Simulate a bot escalation message
curl -X POST http://localhost:5001/api/chat-message \
  -H "Content-Type: application/json" \
  -d '{
    "po_id": "4100260367",
    "sender_type": "bot",
    "message_text": "I have flagged this PO for review",
    "vendor_phone": "9910603920",
    "supplier_name": "Yashoda Gas Service",
    "intent": "PARTIAL",
    "reason": "Vendor can only supply 70% of ordered quantity",
    "escalate": true,
    "admin_message": "MEDIUM PO Exception - Partial Delivery"
  }'

# Expected:
# ✅ [ESCALATION] Created escalation for PO 4100260367
# Check Supabase escalations table — new row should appear
# Check WebSocket — new_escalation event should broadcast