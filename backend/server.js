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

const PORT = process.env.PORT || 5000;
const N8N_WEBHOOK_URL = process.env.N8N_WEBHOOK_URL;

app.use(cors());
app.use(bodyParser.json());

// Initialize table on startup
initDatabase();

// WebSocket Broadcaster
const broadcast = (data) => {
  wss.clients.forEach((client) => {
    if (client.readyState === WebSocket.OPEN) {
      client.send(JSON.stringify(data));
    }
  });
};

// API: Fetch Active POs (Top 5)
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
    const { po_id, sender_type, message_text, vendor_phone, supplier_name } = req.body;
    
    // Save to PostgreSQL (chat_history table)
    const saved = await saveMessage(po_id, sender_type, message_text, vendor_phone);
    
    // Broadcast via WebSocket
    broadcast({ 
      event: 'new_message', 
      po_id, 
      sender_type, 
      message_text, 
      sent_at: saved.sent_at 
    });

    // Forward to n8n if sender is vendor
    if (sender_type === 'vendor' && N8N_WEBHOOK_URL) {
      try {
        await axios.post(N8N_WEBHOOK_URL, {
          po_id,
          supplier_name,
          vendor_phone,
          message_text,
          timestamp: saved.sent_at
        });
      } catch (n8nError) {
        console.error('n8n forwarding failed:', n8nError.message);
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
  console.log(`Server running on port ${PORT}`);
});
