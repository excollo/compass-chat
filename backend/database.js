require('dotenv').config();
const { Pool } = require('pg');
const { v4: uuidv4 } = require('uuid');

// PostgreSQL connection pool optimized for Deployed Environments
const pool = new Pool({
  user: process.env.DB_USER,
  host: process.env.DB_HOST,
  database: process.env.DB_DATABASE,
  password: process.env.DB_PASSWORD,
  port: parseInt(process.env.DB_PORT || '5432'),
  connectionTimeoutMillis: 30000, // Wait 30s before timing out
  idleTimeoutMillis: 30000,       // Close idle clients after 30s
  max: 10,                        // Limit concurrent connections
  ssl: {
    rejectUnauthorized: false
  },
  // Extra reliability for pooler (Supabase port 6543)
  keepalive: true,
  keepaliveInitialDelayMillis: 10000
});

pool.on('error', (err, client) => {
  console.error('⚠️ [DB] Unexpected pool error:', err.message);
  // Optional: Add logic to re-initialize if the pool dies
});


// Setup chat_history table with the SPECIFIC SCHEMA requested
const initDatabase = async () => {
  try {
    const client = await pool.connect();
    console.log('✅ Connected to database for initialization.');
    
    await client.query(`
      CREATE TABLE IF NOT EXISTS chat_history (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        po_num TEXT,
        vendor_code TEXT,
        sender_type TEXT NOT NULL,
        message_text TEXT NOT NULL,
        direction TEXT,
        intent TEXT,
        escalation_required BOOLEAN DEFAULT FALSE,
        vendor_phone TEXT,
        linked_pos JSONB,
        reason TEXT,
        sent_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
      )
    `);

    // Ensure columns exist (for migration if table already existed)
    console.log('🧐 Verifying database columns...');
    await client.query(`ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS intent TEXT`);
    await client.query(`ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS reason TEXT`);
    await client.query(`ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS escalation_required BOOLEAN DEFAULT FALSE`);
    await client.query(`ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS communication_state TEXT DEFAULT 'awaiting'`);
    await client.query(`ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS risk_level TEXT DEFAULT 'none'`);
    await client.query(`ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS confidence_score NUMERIC(4,2)`);
    await client.query(`ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS extracted_eta DATE`);
    await client.query(`ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS shortage_note TEXT`);
    await client.query(`ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS case_type TEXT`);
    await client.query(`ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS priority TEXT DEFAULT 'low'`);
    await client.query(`ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS assigned_spoc TEXT`);
    await client.query(`ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS sla_due_at TIMESTAMP WITH TIME ZONE`);
    await client.query(`ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS sla_breached BOOLEAN DEFAULT FALSE`);
    await client.query(`ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS human_takeover_at TIMESTAMP WITH TIME ZONE`);
    await client.query(`ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS takeover_by TEXT`);
    await client.query(`ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS ai_paused BOOLEAN DEFAULT FALSE`);
    await client.query(`ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS vendor_initiated BOOLEAN DEFAULT FALSE`);
    await client.query(`ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS reminder_count INTEGER DEFAULT 0`);
    await client.query(`ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS linked_pos JSONB`);
    await client.query(`ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS sent_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP`);

    // ── Vendor Threading & PO Binding Migration ──────────────────────────────
    // Add vendor_code as the primary thread identifier
    await client.query(`ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS vendor_code TEXT`);
    
    // Make po_num nullable so vendor-level (pre-disambiguation) messages can be saved without a PO
    await client.query(`ALTER TABLE chat_history ALTER COLUMN po_num DROP NOT NULL`).catch(() => {});
    
    // Add PO binding fields
    await client.query(`ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS bound_po_num TEXT`);
    await client.query(`ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS po_binding_source VARCHAR(20) DEFAULT 'unresolved'`);
    await client.query(`ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS po_binding_confidence NUMERIC(3,2) DEFAULT 0.00`);
    
    console.log('✅ Database initialized correctly.');
    client.release();
  } catch (err) {
    console.error('❌ Error initializing database:', err.message);
  }
};

// Messaging: Save Message
// po_num is now NULLABLE — null means the message belongs to a vendor thread
// but no specific PO has been confirmed yet (multi-PO disambiguation pending).
// Use vendor_code as the durable thread key; po_num fills in after AI confirms it.
const saveMessage = async (po_num, sender_type, message_text, vendor_phone, extra = {}) => {
  const direction = sender_type === 'vendor' ? 'inbound' : 'outbound';
  console.log(`📝 [DB] Saving message | po_num=${po_num || 'NULL'} | vendor_code=${(extra || {}).vendor_code || '-'} | sender=${sender_type}`);
  
  const columns = [
    'po_num', 'vendor_code', 'sender_type', 'message_text', 'direction', 'vendor_phone', 'intent', 
    'reason', 'escalation_required', 'communication_state', 'risk_level', 'confidence_score', 
    'extracted_eta', 'shortage_note', 'case_type', 'priority', 'assigned_spoc', 
    'sla_due_at', 'sla_breached', 'human_takeover_at', 'takeover_by', 'ai_paused', 
    'vendor_initiated', 'reminder_count', 'linked_pos',
    // PO binding fields
    'bound_po_num', 'po_binding_source', 'po_binding_confidence'
  ];

  const extraData = extra || {};
  const values = [
    po_num || null,          // nullable — null until confirmed
    extraData.vendor_code || null,
    sender_type, 
    message_text, 
    direction, 
    vendor_phone, 
    extraData.intent || null, 
    extraData.reason || null,
    extraData.escalation_required || false,
    extraData.communication_state || (sender_type === 'bot' ? 'awaiting' : null),
    extraData.risk_level || 'none',
    extraData.confidence_score || 0.0,
    extraData.extracted_eta || null,
    extraData.shortage_note || null,
    extraData.case_type || null,
    extraData.priority || 'low',
    extraData.assigned_spoc || null,
    extraData.sla_due_at || null,
    extraData.sla_breached || false,
    extraData.human_takeover_at || null,
    extraData.takeover_by || null,
    extraData.ai_paused || false,
    extraData.vendor_initiated || false,
    extraData.reminder_count || 0,
    extraData.linked_pos ? JSON.stringify(extraData.linked_pos) : null,
    // PO binding
    extraData.bound_po_num || null,
    extraData.po_binding_source || 'unresolved',
    extraData.po_binding_confidence != null ? extraData.po_binding_confidence : 0.00
  ];

  const placeholders = values.map((_, i) => `$${i + 1}`).join(', ');
  const query = `
    INSERT INTO chat_history (${columns.join(', ')})
    VALUES (${placeholders})
    RETURNING *;
  `;
  
  const { rows } = await pool.query(query, values);
  console.log(`✅ [DB] Message saved successfully. ID: ${rows[0].id}`);
  return rows[0];
};



// Messaging: Get Chat History (Consolidated by Vendor)
const getChatHistory = async (po_num) => {
  // 1) First try to resolve vendor_code and phone for this PO
  let vendor_code = null;
  let vendor_phone = null;

  const poInfoQuery = `
    SELECT vendor_code, vendor_phone
    FROM selected_open_po_line_items
    WHERE po_num = $1
    LIMIT 1
  `;
  const poInfo = await pool.query(poInfoQuery, [po_num]);
  if (poInfo.rows.length > 0) {
    vendor_code = poInfo.rows[0].vendor_code;
    vendor_phone = poInfo.rows[0].vendor_phone;
  }

  // 2) Fallback to chat history to find phone/code if PO table lookup failed (e.g. for closed POs)
  if (!vendor_code || !vendor_phone) {
    const historyInfoQuery = `
      SELECT vendor_code, vendor_phone
      FROM chat_history
      WHERE po_num = $1
      ORDER BY sent_at DESC
      LIMIT 1
    `;
    const historyInfo = await pool.query(historyInfoQuery, [po_num]);
    if (historyInfo.rows.length > 0) {
      if (!vendor_code) vendor_code = historyInfo.rows[0].vendor_code;
      if (!vendor_phone) vendor_phone = historyInfo.rows[0].vendor_phone;
    }
  }

  // 3) Unified query: filter by vendor_code (primary) OR vendor_phone (fallback)
  // This ensures that messages saved with po_num=NULL still appear in the vendor thread.
  const query = `
    SELECT *
    FROM chat_history
    WHERE
      (vendor_code IS NOT NULL AND vendor_code = $1::TEXT)
      OR (
        vendor_phone IS NOT NULL AND $2::TEXT IS NOT NULL
        AND regexp_replace(vendor_phone, '\\D', '', 'g') = regexp_replace($2::TEXT, '\\D', '', 'g')
      )
      OR po_num = $3::TEXT
    ORDER BY sent_at ASC
  `;
  const { rows } = await pool.query(query, [vendor_code, vendor_phone, po_num]);
  
  return rows.map(r => ({
    ...r,
    // Use the requested po_num as the fallback po_id so these messages 
    // show up in the current thread in the UI even if they aren't bound yet.
    po_id: r.po_num || r.bound_po_num || po_num 
  }));
};

// Messaging: Delete Chat History (Vendor-wide clear)
const deleteChatHistory = async (po_num) => {
  const phoneQuery = `SELECT DISTINCT vendor_phone FROM chat_history WHERE po_num = $1 LIMIT 1`;
  const phoneResult = await pool.query(phoneQuery, [po_num]);
  const vendor_phone = phoneResult.rows.length > 0 ? phoneResult.rows[0].vendor_phone : null;

  if (vendor_phone) {
    const query = `DELETE FROM chat_history WHERE vendor_phone = $1`;
    await pool.query(query, [vendor_phone]);
    console.log(`🗑️ [DB] Deleted unified chat history for Vendor Phone: ${vendor_phone}`);
  } else {
    const query = `DELETE FROM chat_history WHERE po_num = $1`;
    await pool.query(query, [po_num]);
  }
  return true;
};

// Procurement: Fetch data from the live "selected_open_po_line_items"
const getPurchaseOrders = async () => {
  try {
    const tableName = process.env.DB_TABLE_PO || 'selected_open_po_line_items';
    const query = `
      SELECT 
        po_num as po_id,
        vendor_name as supplier_name,
        delivery_date,
        article_description as category,
        CONCAT(po_quantity, ' ', unit) as value,
        vendor_phone,
        status,
        thread_state
      FROM ${tableName} 
      LIMIT 10
    `;
    const { rows } = await pool.query(query);
    
    return rows.map(r => ({
      ...r,
      site: "Compass Site",
      status: r.status || 'pending',
      thread_state: r.thread_state || 'bot_active'
    }));
  } catch (err) {
    console.error('PO Fetch Error:', err.message);
    return [];
  }
};

// HITL: Update Thread State
const updateThreadState = async (po_num, state, metadata = {}) => {
  console.log(`🔄 [DB] Updating thread state to '${state}' for PO: ${po_num}`);
  
  const sets = [`thread_state = $1`, `communication_state = $1`];
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

// PO Binding: update binding fields on an existing message row
const updateMessagePoBinding = async (message_id, bound_po_num, po_binding_confidence, po_binding_source) => {
  const query = `
    UPDATE chat_history
    SET po_num = $1,
        bound_po_num = $1,
        po_binding_confidence = $2,
        po_binding_source = $3
    WHERE id = $4
    RETURNING id, po_num, bound_po_num, po_binding_source, po_binding_confidence;
  `;
  const { rows } = await pool.query(query, [bound_po_num, po_binding_confidence, po_binding_source, message_id]);
  console.log(`🔗 [DB] PO binding updated for message ${message_id} → ${bound_po_num} (${po_binding_source})`);
  return rows[0];
};

// PO Binding: fetch all open POs for a vendor phone (for binding logic in server)
const getOpenPOsForVendor = async (vendor_phone) => {
  if (!vendor_phone) return [];
  const query = `
    SELECT DISTINCT po_num, vendor_name, vendor_code, delivery_date, status, thread_state
    FROM selected_open_po_line_items
    WHERE (
      vendor_phone IS NOT NULL
      AND regexp_replace(vendor_phone, '\\D', '', 'g') = regexp_replace($1, '\\D', '', 'g')
    )
    AND (status IS NULL OR LOWER(status) NOT IN ('closed', 'delivered', 'completed'))
    ORDER BY delivery_date ASC;
  `;
  const { rows } = await pool.query(query, [vendor_phone]);
  return rows;
};

// Vendor: get vendor_code for a phone number (used to key the chat thread)
const getVendorCodeForPhone = async (vendor_phone) => {
  if (!vendor_phone) return null;
  const query = `
    SELECT DISTINCT vendor_code
    FROM selected_open_po_line_items
    WHERE vendor_phone IS NOT NULL
      AND regexp_replace(vendor_phone, '\\D', '', 'g') = regexp_replace($1, '\\D', '', 'g')
      AND vendor_code IS NOT NULL
    LIMIT 1;
  `;
  const { rows } = await pool.query(query, [vendor_phone]);
  return rows[0]?.vendor_code || null;
};

module.exports = {
  saveMessage,
  getChatHistory,
  deleteChatHistory,
  getPurchaseOrders,
  updateThreadState,
  initDatabase,
  updateMessagePoBinding,
  getOpenPOsForVendor,
  getVendorCodeForPhone,
  pool
};
