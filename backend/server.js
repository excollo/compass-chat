require('dotenv').config();
const express = require('express');
const http = require('http');
const WebSocket = require('ws');
const axios = require('axios');
const cors = require('cors');
const bodyParser = require('body-parser');
const { saveMessage, getChatHistory, deleteChatHistory, getPurchaseOrders, updateThreadState, initDatabase } = require('./database');
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
  // If it's already ISO format (contains T or starts with YYYY-MM-DD)
  if (dateStr.includes('T') || /^\d{4}-\d{2}-\d{2}/.test(dateStr)) {
    return dateStr;
  }
  // Try parsing DD-MM-YYYY
  const parts = dateStr.split('-');
  if (parts.length === 3 && parts[2].length === 4) {
    // DD-MM-YYYY -> YYYY-MM-DD
    return `${parts[2]}-${parts[1]}-${parts[0]}`;
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
      confidence_score, linked_pos
    } = req.body;
    
    console.log(`📩 [BACKEND] Message received from ${sender_type} for PO: ${po_id}`);

    // Prepare extra data for DB save
    const extraData = {
      intent, reason, escalation_required, communication_state, risk_level, 
      priority, sla_due_at, case_type, extracted_eta, shortage_note,
      ai_paused, vendor_initiated, confidence_score, linked_pos
    };

    // Save to PostgreSQL (chat_history table)
    const saved = await saveMessage(po_id, sender_type, message_text, vendor_phone, extraData);
    
    // Identify all sibling POs for this vendor to ensure real-time sync across all views
    let siblingPoIds = [po_id];
    try {
      const { data: siblingPos } = await supabase
        .from('selected_open_po_line_items')
        .select('po_num')
        .eq('vendor_phone', vendor_phone);
      
      if (siblingPos && siblingPos.length > 0) {
        siblingPoIds = [...new Set(siblingPos.map(p => p.po_num))];
      }
    } catch (err) {
      console.error(`⚠️ [SYNC] Failed to fetch sibling POs:`, err.message);
    }

    // Broadcast to every sibling PO ID
    siblingPoIds.forEach(targetId => {
      broadcast({ 
        event: 'new_message', 
        po_id: targetId, 
        sender_type, 
        message_text, 
        ...extraData,
        admin_message: admin_message || '',
        sent_at: saved.sent_at 
      });
    });

    // If bot flagged escalation — create record in Supabase escalations table
    if (escalation_required === true && sender_type === 'bot' && intent) {
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
        .update({ thread_state: 'escalated', communication_state: 'exception_detected' })
        .eq('po_num', po_id);
    }

    // Forward vendor messages to Python orchestration backend
    // Python will check thread_state and decide whether bot should respond
    // Operator messages are NOT forwarded — they are human-to-vendor directly
    if (sender_type === 'vendor') {
      const PYTHON_BACKEND_URL = (process.env.PYTHON_BACKEND_URL || 'http://localhost:8000').trim();
      console.log(`🚀 [BACKEND] Forwarding vendor message to Python: ${PYTHON_BACKEND_URL}/webhook/chat`);
      try {
        const resp = await axios.post(`${PYTHON_BACKEND_URL}/webhook/chat`, {
          session_id: po_id,
          po_id,
          supplier_name: supplier_name || "",
          vendor_phone: vendor_phone || "",
          message_text: message_text || "",
          timestamp: saved.sent_at
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

