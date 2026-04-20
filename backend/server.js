require('dotenv').config();
const express = require('express');
const http = require('http');
const WebSocket = require('ws');
const axios = require('axios');
const cors = require('cors');
const bodyParser = require('body-parser');
const { saveMessage, getChatHistory, deleteChatHistory, getPurchaseOrders, updateThreadState, initDatabase, updateMessagePoBinding, getOpenPOsForVendor, getVendorCodeForPhone } = require('./database');
const { createClient } = require('@supabase/supabase-js');

const supabase = createClient(
  process.env.SUPABASE_URL,
  process.env.SUPABASE_SERVICE_KEY
);

console.log("📡 [REALTIME] Initializing subscription early...");
supabase
  .channel('po-updates')
  .on(
    'postgres_changes',
    {
      event: 'UPDATE',
      schema: 'public',
      table: 'selected_open_po_line_items'
    },
    async (payload) => {
      try {
        console.log(`📡 [REALTIME] Event received: ${payload.eventType}`);
        const record = payload.new;
        const old_record = payload.old || {};
        const po_id = record.po_num;
        const changes = [];

        if (record.delivery_date !== old_record.delivery_date) {
          changes.push(`Delivery Date changed to ${record.delivery_date}`);
        }
        if (record.status !== old_record.status) {
          changes.push(`Status changed to ${record.status}`);
        }
        if (record.po_quantity !== old_record.po_quantity) {
          changes.push(`Ordered Quantity changed to ${record.po_quantity}`);
        }
        if (record.delivered_quantity !== old_record.delivered_quantity) {
          changes.push(`Delivered Quantity changed to ${record.delivered_quantity}`);
        }

        if (changes.length > 0) {
          console.log(`🔔 [PROACTIVE] Triggering bot for PO ${po_id} updates.`);
          const PYTHON_URL = process.env.PYTHON_BACKEND_URL || 'http://localhost:8000';
          await axios.post(`${PYTHON_URL}/webhook/proactive-update`, {
            po_id,
            supplier_name: record.vendor_name,
            vendor_phone: record.vendor_phone,
            changes: changes
          });
        }
      } catch (err) {
        console.error('❌ [REALTIME] Listener Error:', err.message);
      }
    }
  )
  .subscribe((status) => {
    console.log(`📡 [REALTIME] Subscription Status: ${status}`);
  });

// Maps bot intent values to escalations table reason values
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

// Helper to convert DD-MM-YYYY to YYYY-MM-DD for Postgres
function formatDateForPostgres(dateStr) {
  if (!dateStr) return null;
  // If it's already a Date object (happens with pg driver for date/timestamp columns)
  if (dateStr instanceof Date) {
    return dateStr.toISOString().split('T')[0];
  }
  // If it's already ISO format (contains T or starts with YYYY-MM-DD)
  if (typeof dateStr === 'string' && (dateStr.includes('T') || /^\d{4}-\d{2}-\d{2}/.test(dateStr))) {
    return dateStr;
  }
  // Try parsing DD-MM-YYYY
  if (typeof dateStr === 'string') {
    const parts = dateStr.split('-');
    if (parts.length === 3 && parts[2].length === 4) {
      // DD-MM-YYYY -> YYYY-MM-DD
      return `${parts[2]}-${parts[1]}-${parts[0]}`;
    }
  }
  return dateStr;
}

async function createEscalationInSupabase(poData) {
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
    const { data: poRecord, error: poError } = await supabase
      .from('selected_open_po_line_items')
      .select('*')
      .eq('po_num', po_id)
      .limit(1)
      .maybeSingle();

    if (poError || !poRecord) {
      console.error(`❌ [ESCALATION] PO not found or query failed for: ${po_id}`, poError?.message || '');
      return null;
    }

    const intentKey = (intent || '').toUpperCase().trim();
    const escalationReason = INTENT_TO_REASON[intentKey] || 'other';
    const priority = INTENT_TO_PRIORITY[intentKey] || 'medium';
    const category = INTENT_TO_CATEGORY[intentKey] || 'Operation';

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
      delivery_date:        formatDateForPostgres(poRecord.delivery_date) || new Date().toISOString().split('T')[0],
      document_date:        formatDateForPostgres(poRecord.po_date) || null,
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

const app = express();
const server = http.createServer(app);
const wss = new WebSocket.Server({ server });

const PORT = parseInt(process.env.PORT || '5001');
const N8N_WEBHOOK_URL = (process.env.N8N_WEBHOOK_URL || "https://n8n-excollo.azurewebsites.net/webhook-test/6d06fe42-147d-4c86-9f21-68af1d782d46").trim();

app.use(cors());
app.use(bodyParser.json());

// Initialize table on startup
initDatabase();

// Health Check Endpoint
app.get('/health', (req, res) => {
  res.status(200).json({ status: 'ok', timestamp: new Date().toISOString() });
});

// WebSocket Broadcaster
const broadcast = (data) => {
  wss.clients.forEach((client) => {
    if (client.readyState === WebSocket.OPEN) {
      client.send(JSON.stringify(data));
    }
  });
};

// API: Fetch Active POs
app.get('/api/purchase-orders', async (req, res) => {
  try {
    const pos = await getPurchaseOrders();
    res.json(pos);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// API: Fetch chat history for a PO
app.get('/api/chat-history', async (req, res) => {
  try {
    const { po_id } = req.query;
    if (!po_id) return res.status(400).json({ error: 'po_id is required' });
    const history = await getChatHistory(po_id);
    res.json(history);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// API: Clear/Refresh chat history for a PO
app.delete('/api/chat-history', async (req, res) => {
  try {
    const { po_id } = req.query;
    if (!po_id) return res.status(400).json({ error: 'po_id is required' });
    
    await deleteChatHistory(po_id);
    
    // Broadcast clear event so UI components can reset instantly
    broadcast({ event: 'clear_chat', po_id });

    // Also clear Python agent's in-memory session (resolved PO tracking + message history)
    const PYTHON_URL = (process.env.PYTHON_BACKEND_URL || 'http://localhost:8000').trim();
    try {
      await axios.post(`${PYTHON_URL}/webhook/clear-session`, { session_id: po_id });
      console.log(`🧠 [BACKEND] Python session cleared for PO: ${po_id}`);
    } catch (pyErr) {
      console.warn(`⚠️ [BACKEND] Python session clear failed (non-fatal): ${pyErr.message}`);
    }
    
    res.json({ success: true, message: 'Conversation memory cleared' });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// API: Save and forward message
app.post('/api/chat-message', async (req, res) => {
  try {
    const { 
      po_id, sender_type, message_text, vendor_phone, supplier_name, 
      intent, reason, escalation_required, admin_message, conversation_complete,
      communication_state, risk_level, priority, sla_due_at, case_type,
      extracted_eta, shortage_note, ai_paused, vendor_initiated,
      confidence_score, linked_pos,
      // PO binding (sent by Python agent on bot replies)
      bound_po_num, po_binding_source, po_binding_confidence
    } = req.body;
    
    console.log(`📩 [BACKEND] Message received from ${sender_type} for PO: ${po_id}`);

    // ── PO Binding: resolve binding for INBOUND vendor messages ───────────────
    let resolvedBoundPoNum = bound_po_num || null;
    let resolvedBindingSource = po_binding_source || 'unresolved';
    let resolvedBindingConfidence = po_binding_confidence != null ? po_binding_confidence : 0.00;
    // Vendor-code for this vendor (the durable thread key, vendor_code over phone)
    let vendorCode = req.body.vendor_code || null;

    if (sender_type === 'vendor' && vendor_phone) {
      try {
        const openPos = await getOpenPOsForVendor(vendor_phone);
        // Always resolve vendor_code from DB for inbound messages
        if (!vendorCode && openPos.length > 0) {
          vendorCode = openPos[0].vendor_code || null;
        }
        if (!vendorCode) {
          vendorCode = await getVendorCodeForPhone(vendor_phone);
        }

        // Respect explicit PO binding provided by the caller (UI-selected PO thread).
        if (!resolvedBoundPoNum) {
          if (openPos.length === 1) {
            // Only one open PO → bind automatically
            resolvedBoundPoNum = openPos[0].po_num;
            resolvedBindingSource = 'inferred';
            resolvedBindingConfidence = 0.95;
            console.log(`🔗 [BINDING] Single PO vendor → auto-bound to ${resolvedBoundPoNum} | vendor_code=${vendorCode}`);
          } else if (openPos.length > 1) {
            // Multiple open POs → unresolved until AI clarifies
            // IMPORTANT: do NOT assign resolvedBoundPoNum — leave null
            resolvedBoundPoNum = null;
            resolvedBindingSource = 'unresolved';
            resolvedBindingConfidence = 0.00;
            console.log(`⚠️ [BINDING] Multi-PO vendor (${openPos.length} POs) → binding unresolved | vendor_code=${vendorCode}`);
          }
        } else {
          console.log(`🔗 [BINDING] Using explicit bound PO ${resolvedBoundPoNum} | source=${resolvedBindingSource}`);
        }
      } catch (bindErr) {
        console.error('⚠️ [BINDING] PO lookup failed:', bindErr.message);
      }
    } else if (sender_type === 'bot') {
      // Bot replies carry vendor_code from Python agent
      if (!vendorCode) vendorCode = await getVendorCodeForPhone(vendor_phone).catch(() => null);
      // Bot binding source: use what Python sent; upgrade if we have a bound_po but no source
      if (!resolvedBindingSource || resolvedBindingSource === 'unresolved') {
        if (resolvedBoundPoNum) {
          resolvedBindingSource = 'inferred';
          resolvedBindingConfidence = 0.90;
        }
      }
    }

    // Evaluate escalation FIRST so we can flag the message properly in the DB
    const EXCEPTION_INTENTS = new Set(['PARTIAL', 'DELAYED', 'REJECTED', 'PRICE_DISPUTE', 'PRICE_UPDATE', 'PAYMENT_ISSUE', 'QUALITY_ISSUE', 'PO_CANCELLATION']);
    const safeIntent = (intent || '').toUpperCase().trim();
    
    const isEscalationReq = escalation_required === true || String(escalation_required).toLowerCase() === 'true';
    const isConvComplete  = conversation_complete === true || String(conversation_complete).toLowerCase() === 'true';

    const shouldEscalate = !!(
      (isEscalationReq && sender_type === 'bot' && safeIntent) ||
      (isConvComplete && sender_type === 'bot' && safeIntent && EXCEPTION_INTENTS.has(safeIntent))
    );

    // ── GUARD: Never create a case when PO binding is unresolved ────────────
    // Escalation requires a confirmed bound_po_num; otherwise the case would target the wrong PO.
    const bindingConfirmed = resolvedBindingSource !== 'unresolved' && !!resolvedBoundPoNum;
    const canEscalate = shouldEscalate && bindingConfirmed;
    if (shouldEscalate && !bindingConfirmed) {
      console.warn(`⛔ [ESCALATION] Blocked — PO binding unresolved for message on PO: ${po_id}. Case NOT created.`);
    }

    const finalEscalationFlag = canEscalate;


    const extraData = {
      intent, reason, escalation_required: finalEscalationFlag, communication_state, risk_level, 
      priority, sla_due_at, case_type, extracted_eta, shortage_note,
      ai_paused, vendor_initiated, confidence_score, linked_pos,
      // PO binding
      bound_po_num: resolvedBoundPoNum,
      po_binding_source: resolvedBindingSource,
      po_binding_confidence: resolvedBindingConfidence,
      // Vendor scope
      vendor_code: vendorCode
    };

    // ── Save to PostgreSQL ───────────────────────────────────────────────
    // For vendor messages with unresolved multi-PO binding:
    //   po_num = NULL (not a specific PO yet)
    //   vendor_code = the vendor-level thread key
    // For confirmed bindings and bot messages:
    //   po_num = the confirmed PO number
    const savePONum = (sender_type === 'vendor' && resolvedBindingSource === 'unresolved')
      ? null
      : (resolvedBoundPoNum || po_id || null);

    const saved = await saveMessage(savePONum, sender_type, message_text, vendor_phone, extraData);
    console.log(`💾 [DB] Saved | id=${saved.id} | po_num=${savePONum || 'NULL'} | vendor_code=${vendorCode || '-'} | source=${resolvedBindingSource}`);
    
    // Broadcast only to the authoritative PO thread.
    const targetPoId = savePONum || po_id;
    broadcast({ 
      event: 'new_message', 
      po_id: targetPoId, 
      sender_type, 
      message_text, 
      ...extraData,
      admin_message: admin_message || '',
      sent_at: saved.sent_at 
    });

    // If bot flagged escalation AND binding is confirmed — create record in Supabase escalations table
    if (canEscalate) {
      // Use the authoritatively bound PO number, not the original po_id
      const escalationPoId = resolvedBoundPoNum || po_id;
      console.log(`🚨 [ESCALATION] Triggered for bound PO ${escalationPoId} (original: ${po_id}) — intent: ${intent} | binding: ${resolvedBindingSource}`);
      
      const escalationRecord = await createEscalationInSupabase({
        po_id: escalationPoId,
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
          po_num:         escalationPoId,
          vendor_name:    escalationRecord.vendor_name,
          reason:         escalationRecord.escalation_reason,
          reason_detail:  escalationRecord.reason_detail,
          priority:       escalationRecord.priority,
          category:       escalationRecord.category,
          ai_summary:     escalationRecord.ai_summary,
          created_at:     escalationRecord.escalation_created_at
        });

        // ONLY update thread_state to escalated if row creation succeeded
        console.log(`🚨 [ESCALATION] Updating PO table state to escalated for ${escalationPoId}`);
        await supabase
          .from('selected_open_po_line_items')
          .update({ thread_state: 'escalated', communication_state: 'exception_detected' })
          .eq('po_num', escalationPoId);
      } else {
        console.error(`❌ [ESCALATION] Failed to create escalation record for PO ${escalationPoId}. PO table NOT updated.`);
      }
    }

    // Forward vendor messages to Python orchestration backend
    if (sender_type === 'vendor') {
      const PYTHON_BACKEND_URL = (process.env.PYTHON_BACKEND_URL || 'http://localhost:8000').trim();
      console.log(`🚀 [BACKEND] Forwarding vendor message to Python: ${PYTHON_BACKEND_URL}/webhook/chat`);
      try {
        const resp = await axios.post(`${PYTHON_BACKEND_URL}/webhook/chat`, {
          session_id: po_id,
          po_id,
          supplier_name: supplier_name || "",
          vendor_phone: vendor_phone || "",
          vendor_code: vendorCode || "",   // ← now passed so Python uses it as session key
          message_text: message_text || "",
          timestamp: saved.sent_at,
          inbound_message_id: saved.id || ""  // for binding back-update
        });
        console.log(`✅ [BACKEND] Python Response: ${resp.status}`);
      } catch (err) {
        console.error(`❌ [BACKEND] Python Webhook Error: ${err.message}`);
      }
    }



    res.json(saved);
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// API: Take over conversation (HITL)
app.post('/api/takeover', async (req, res) => {
  try {
    const { po_num, operator_name } = req.body;
    if (!po_num) return res.status(400).json({ error: 'po_num is required' });
    console.log(`🤝 [BACKEND] Operator ${operator_name || 'Admin'} taking over PO: ${po_num}`);
    // 1. Update thread_state in Postgres
    await updateThreadState(po_num, 'human_controlled', {
      taken_over_at: new Date().toISOString(),
      taken_over_by: operator_name || 'Admin'
    });
    // 2. Save system message
    await saveMessage(po_num, 'system', `Operator ${operator_name || 'Admin'} took over. Bot is paused.`, '', {});
    // 3. Broadcast update to all connected clients
    broadcast({ event: 'thread_state_change', po_id: po_num, thread_state: 'human_controlled' });
    res.json({ success: true, thread_state: 'human_controlled' });
  } catch (error) {
    console.error('❌ [BACKEND] Takeover Error:', error.message);
    res.status(500).json({ error: error.message });
  }
});

// API: Hand back to bot (HITL)
app.post('/api/handback', async (req, res) => {
  try {
    const { po_num, operator_name } = req.body;
    if (!po_num) return res.status(400).json({ error: 'po_num is required' });
    console.log(`🤖 [BACKEND] Handing back PO: ${po_num} to Bot`);
    // Forward to Python backend for summary generation
    const PYTHON_URL = process.env.PYTHON_BACKEND_URL || 'http://localhost:8000';
    try {
      await axios.post(`${PYTHON_URL}/webhook/handback`, { po_id: po_num });
    } catch (err) {
      console.error(`❌ [BACKEND] Python Handback Warning: ${err.message}`);
      // Fallback: update state even if python summary fails
      await updateThreadState(po_num, 'bot_active');
    }
    // Save system message
    await saveMessage(po_num, 'system', `Bot resumed by operator.`, '', {});
    // Broadcast update
    broadcast({ event: 'thread_state_change', po_id: po_num, thread_state: 'bot_active' });
    res.json({ success: true, thread_state: 'bot_active' });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// API: Update PO binding on an existing message (called by Python agent after clarification)
app.patch('/api/message/:id/po-binding', async (req, res) => {
  try {
    const { id } = req.params;
    const { bound_po_num, po_binding_confidence, po_binding_source } = req.body;
    if (!id) return res.status(400).json({ error: 'message id is required' });
    if (!bound_po_num) return res.status(400).json({ error: 'bound_po_num is required' });

    const updated = await updateMessagePoBinding(
      id,
      bound_po_num,
      po_binding_confidence != null ? po_binding_confidence : 0.90,
      po_binding_source || 'inferred'
    );
    console.log(`🔗 [BACKEND] PO binding updated for message ${id} → ${bound_po_num}`);
    res.json({ success: true, updated });
  } catch (error) {
    console.error('❌ [BACKEND] PO Binding Update Error:', error.message);
    res.status(500).json({ error: error.message });
  }
});

// API: PO Update Webhook (backup)
app.post('/api/webhook/po-update', async (req, res) => {
  try {
    const { record, old_record, type } = req.body;
    
    if (type !== 'UPDATE') return res.status(200).json({ status: 'ignored' });
    
    const po_id = record.po_num;
    const changes = [];
    
    // Check specific fields for changes
    if (record.delivery_date !== old_record.delivery_date) {
      changes.push(`Delivery Date changed from ${old_record.delivery_date} to ${record.delivery_date}`);
    }
    if (record.status !== old_record.status) {
      changes.push(`Status changed from ${old_record.status} to ${record.status}`);
    }
    if (record.po_quantity !== old_record.po_quantity) {
      changes.push(`Ordered Quantity changed from ${old_record.po_quantity} to ${record.po_quantity}`);
    }
    if (record.delivered_quantity !== old_record.delivered_quantity) {
      changes.push(`Delivered Quantity changed from ${old_record.delivered_quantity} to ${record.delivered_quantity}`);
    }

    if (changes.length > 0) {
      console.log(`🔔 [ESCALATION] PO ${po_id} updated. Triggering proactive bot notification.`);
      
      // Forward to Python agent for AI-generated proactive message
      const PYTHON_URL = process.env.PYTHON_BACKEND_URL || 'http://localhost:8000';
      await axios.post(`${PYTHON_URL}/webhook/proactive-update`, {
        po_id,
        supplier_name: record.vendor_name,
        vendor_phone: record.vendor_phone,
        changes: changes
      });
    }

    res.json({ success: true });
  } catch (error) {
    console.error('❌ [WEBHOOK] PO Update Error:', error.message);
    res.status(500).json({ error: error.message });
  }
});

wss.on('connection', (ws) => {
  console.log('Client connected to WebSocket');
});

server.listen(PORT, () => {
  console.log(`\n🚀 Backend Server running on port ${PORT}`);
  console.log(`🔗 Active Webhook: ${N8N_WEBHOOK_URL}`);
  console.log('--------------------------------------------------\n');
});

