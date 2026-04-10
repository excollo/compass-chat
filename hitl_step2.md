
Prompt
Make the following changes to server.js only. Do not change database.js.
Do not change any existing endpoints except the one modification below.

Change 1 — Replace n8n forwarding with direct Python call
In the existing /api/chat-message POST handler, find this block:
js// Forward to n8n if sender is vendor
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
Replace it with:
js// Forward vendor messages to Python orchestration backend
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
// sender_type === 'operator': already saved and broadcast above — nothing more needed
// sender_type === 'bot': already saved and broadcast above — nothing more needed

Change 2 — Add PYTHON_BACKEND_URL to .env
Add this line to backend/.env:
PYTHON_BACKEND_URL=http://localhost:8000

Change 3 — Keep N8N_WEBHOOK_URL in .env but it is no longer used
Leave the N8N_WEBHOOK_URL variable in .env for now in case it is needed
later. The code change above replaces all usage of it.

Do NOT change

WebSocket broadcast logic
saveMessage function calls
/api/chat-history endpoint
/api/purchase-orders endpoint
database.js
WebSocket connection handler


How to test after building
bash# 1. Start server.js
cd backend && node server.js

# 2. POST an operator message — should NOT reach Python
curl -X POST http://localhost:5001/api/chat-message \
  -H "Content-Type: application/json" \
  -d '{"po_id":"4100260367","sender_type":"operator","message_text":"Hi from admin","vendor_phone":"","supplier_name":""}'

# Expected: saved to DB, broadcast via WebSocket, NO Python call in logs

# 3. POST a vendor message — should reach Python
curl -X POST http://localhost:5001/api/chat-message \
  -H "Content-Type: application/json" \
  -d '{"po_id":"4100260367","sender_type":"vendor","message_text":"Yes will deliver","vendor_phone":"9999999999","supplier_name":"Test Vendor"}'

# Expected: saved, broadcast, Python called, Python checks thread_state
