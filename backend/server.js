require('dotenv').config();
const express = require('express');
const http = require('http');
const WebSocket = require('ws');
const axios = require('axios');
const cors = require('cors');
const bodyParser = require('body-parser');
const { saveMessage, getChatHistory, deleteChatHistory, getPurchaseOrders, updateThreadState, initDatabase } = require('./database');

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
    const { po_id, sender_type, message_text, vendor_phone, supplier_name, intent, escalate, admin_message, conversation_complete } = req.body;
    
    console.log(`📩 [BACKEND] Message received from ${sender_type} for PO: ${po_id}`);

    // Save to PostgreSQL (chat_history table)
    const saved = await saveMessage(po_id, sender_type, message_text, vendor_phone, intent, escalate);
    
    // Broadcast via WebSocket
    broadcast({ 
      event: 'new_message', 
      po_id, 
      sender_type, 
      message_text, 
      intent: intent || null,
      escalate: escalate || false,
      admin_message: admin_message || '',
      sent_at: saved.sent_at 
    });

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
          supplier_name,
          vendor_phone,
          message_text,
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
    await saveMessage(po_num, 'system', `Operator ${operator_name || 'Admin'} took over. Bot is paused.`, '', null, false);
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
    await saveMessage(po_num, 'system', `Bot resumed by operator.`, '', null, false);
    // Broadcast update
    broadcast({ event: 'thread_state_change', po_id: po_num, thread_state: 'bot_active' });
    res.json({ success: true, thread_state: 'bot_active' });
  } catch (error) {
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

