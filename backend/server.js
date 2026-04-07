require('dotenv').config();
const express = require('express');
const http = require('http');
const WebSocket = require('ws');
const axios = require('axios');
const cors = require('cors');
const bodyParser = require('body-parser');
const { saveMessage, getChatHistory, getPurchaseOrders, initDatabase } = require('./database');

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

    // Forward to n8n if sender is vendor
    if (sender_type === 'vendor') {
      if (N8N_WEBHOOK_URL) {
        console.log(`🚀 [BACKEND] Forwarding to n8n: ${N8N_WEBHOOK_URL}`);
        try {
          const resp = await axios.post(N8N_WEBHOOK_URL, {
            session_id: po_id, 
            po_id,
            supplier_name,
            vendor_phone,
            message_text,
            timestamp: saved.sent_at
          });
          console.log(`✅ [BACKEND] n8n Response: ${resp.status} ${JSON.stringify(resp.data)}`);
        } catch (err) {
          console.error(`❌ [BACKEND] n8n Webhook Error: ${err.message}`);
        }
      } else {
        console.error("⚠️ [BACKEND] N8N_WEBHOOK_URL is not defined!");
      }
    }



    res.json(saved);
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

