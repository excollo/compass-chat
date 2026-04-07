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
        po_num TEXT NOT NULL,
        sender_type TEXT NOT NULL,
        message_text TEXT NOT NULL,
        direction TEXT,
        intent TEXT,
        escalation_required BOOLEAN DEFAULT FALSE,
        vendor_phone TEXT,
        sent_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
      )
    `);

    // Ensure columns exist (for migration if table already existed)
    console.log('🧐 Verifying database columns...');
    await client.query(`ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS intent TEXT`);
    await client.query(`ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS escalation_required BOOLEAN DEFAULT FALSE`);
    
    console.log('✅ Database initialized correctly.');
    client.release();
  } catch (err) {
    console.error('❌ Error initializing database:', err.message);
  }
};

// Messaging: Save Message
const saveMessage = async (po_num, sender_type, message_text, vendor_phone, intent = null, escalation_required = false) => {
  const direction = sender_type === 'vendor' ? 'inbound' : 'outbound';
  console.log(`📝 [DB] Saving message to chat_history...`);
  
  const query = `
    INSERT INTO chat_history (po_num, sender_type, message_text, direction, vendor_phone, intent, escalation_required)
    VALUES ($1, $2, $3, $4, $5, $6, $7)
    RETURNING *;
  `;
  const values = [po_num, sender_type, message_text, direction, vendor_phone, intent, escalation_required];
  
  const { rows } = await pool.query(query, values);
  console.log(`✅ [DB] Message saved successfully. ID: ${rows[0].id}`);
  return rows[0];
};



// Messaging: Get Chat History (Mapping and Sorting)
const getChatHistory = async (po_num) => {
  const query = `SELECT * FROM chat_history WHERE po_num = $1 ORDER BY sent_at ASC`;
  const { rows } = await pool.query(query, [po_num]);
  return rows.map(r => ({
    ...r,
    po_id: r.po_num // Ensure internal frontend mapping stays intact
  }));
};

// Messaging: Delete Chat History (Refresh Memory)
const deleteChatHistory = async (po_num) => {
  const query = `DELETE FROM chat_history WHERE po_num = $1`;
  await pool.query(query, [po_num]);
  console.log(`🗑️ [DB] Deleted chat history for PO: ${po_num}`);
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
        status
      FROM ${tableName} 
      LIMIT 10
    `;
    const { rows } = await pool.query(query);
    
    return rows.map(r => ({
      ...r,
      site: "Compass Site",
      status: r.status || 'pending'
    }));
  } catch (err) {
    console.error('PO Fetch Error:', err.message);
    return [];
  }
};

module.exports = {
  saveMessage,
  getChatHistory,
  deleteChatHistory,
  getPurchaseOrders,
  initDatabase,
  pool
};
