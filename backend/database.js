require('dotenv').config();
const { Pool } = require('pg');
const { v4: uuidv4 } = require('uuid');

// PostgreSQL connection pool optimized for Supabase/Remote hosts
const pool = new Pool({
  user: process.env.DB_USER,
  host: process.env.DB_HOST,
  database: process.env.DB_DATABASE,
  password: process.env.DB_PASSWORD,
  port: parseInt(process.env.DB_PORT || '5432'),
  connectionTimeoutMillis: 10000,
  // remote databases like Supabase often REQUIRE SSL
  ssl: {
    rejectUnauthorized: false
  }
});

// Explicit connection test on startup
const testConnection = async () => {
  try {
    const client = await pool.connect();
    console.log(`✅ Database connected successfully to ${process.env.DB_DATABASE} on ${process.env.DB_HOST}`);
    client.release();
    return true;
  } catch (err) {
    console.error(`❌ DATABASE CONNECTION ERROR: 
    - Check your credentials in .env
    - Ensure your password is correct
    - For Supabase, check if you have allowed your IP in their dashboard (if using IP restrictions)
    - Specific Error: ${err.message}`);
    return false;
  }
};

pool.on('error', (err, client) => {
  console.error('Unexpected error on idle client', err);
});

// Setup chat_history table
const initDatabase = async () => {
  if (!(await testConnection())) return;
  
  try {
    await pool.query(`
      CREATE TABLE IF NOT EXISTS chat_history (
        message_id UUID PRIMARY KEY,
        po_id TEXT NOT NULL,
        sender_type TEXT NOT NULL,
        message_text TEXT NOT NULL,
        vendor_phone TEXT,
        sent_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
      )
    `);
    console.log('Database initialized: chat_history table is ready.');
  } catch (err) {
    console.error('Error initializing database:', err);
  }
};

// Messaging: Save Message
const saveMessage = async (po_id, sender_type, message_text, vendor_phone) => {
  const id = uuidv4();
  const query = `
    INSERT INTO chat_history (message_id, po_id, sender_type, message_text, vendor_phone)
    VALUES ($1, $2, $3, $4, $5)
    RETURNING *;
  `;
  const values = [id, po_id, sender_type, message_text, vendor_phone];
  const { rows } = await pool.query(query, values);
  return rows[0];
};

// Messaging: Get Chat History
const getChatHistory = async (po_id) => {
  const query = `SELECT * FROM chat_history WHERE po_id = $1 ORDER BY sent_at ASC`;
  const { rows } = await pool.query(query, [po_id]);
  return rows;
};

// Procurement: Fetch data from the live "selected_open_po_line_items" (or ENV)
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
      site: "Compass Site", // Static placeholder
      status: r.status || 'pending'
    }));
  } catch (err) {
    console.error('PO Fetch Error (Ensure table exists):', err.message);
    return [];
  }
};

module.exports = {
  saveMessage,
  getChatHistory,
  getPurchaseOrders,
  initDatabase,
  pool
};
