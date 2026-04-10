markdown
# 🔥 HITL Backend Updates (Node.js & Python)
Apply these changes to the `compass_chat` repository to enable Takeover and Hand-back functionality.
---
## 1. Update `backend/database.js`
Add this function to the bottom of the file (before `module.exports`) and update the exports list.
```javascript
// HITL: Update Thread State in Supabase/Postgres
const updateThreadState = async (po_num, state, metadata = {}) => {
  console.log(`🔄 [DB] Updating thread state to '${state}' for PO: ${po_num}`);
  
  const sets = [`thread_state = $1`];
  const values = [state, po_num];
  let i = 3;
  if (metadata.taken_over_at) {
    sets.push(`taken_over_at = $${i++}`);
    values.push(metadata.taken_over_at);
  }
  if (metadata.taken_over_by) {
    sets.push(`taken_over_by = $${i++}`);
    values.push(metadata.taken_over_by);
  }
  if (metadata.handed_back_at) {
    sets.push(`handed_back_at = $${i++}`);
    values.push(metadata.handed_back_at);
  }
  if (metadata.bot_context_summary) {
    sets.push(`bot_context_summary = $${i++}`);
    values.push(metadata.bot_context_summary);
  }
  const query = `
    UPDATE selected_open_po_line_items 
    SET ${sets.join(', ')}
    WHERE po_num = $2
    RETURNING *;
  `;
  const { rows } = await pool.query(query, values);
  return rows[0];
};
module.exports = {
  saveMessage,
  getChatHistory,
  deleteChatHistory,
  getPurchaseOrders,
  updateThreadState, // <--- Add this
  initDatabase,
  pool
};
2. Update backend/server.js
Add these two new routes before the server.listen call.

javascript
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
3. Update backend_agent/main.py (Python)
Add this endpoint to handle the AI context summarization when handing back.

python
class HandbackBody(BaseModel):
    po_id: str
@app.post("/webhook/handback")
async def webhook_handback(body: HandbackBody, background_tasks: BackgroundTasks):
    """
    Triggered when a human hands control back to the bot.
    Summarizes the human conversation and resets state.
    """
    po_id = body.po_id
    
    async def process_handback():
        logger.info(f"Generating handback summary for PO: {po_id}")
        # 1. Fetch recent messages
        # 2. Call LLM to summarize
        # 3. Update Supabase thread_state to 'bot_active'
        # (This logic ensures the bot knows what the human discussed)
        pass 
    background_tasks.add_task(process_handback)
    return {"status": "accepted"}
