require('dotenv').config();
const { Pool } = require('pg');

const pool = new Pool({
  user: process.env.DB_USER,
  host: process.env.DB_HOST,
  database: process.env.DB_DATABASE,
  password: process.env.DB_PASSWORD,
  port: parseInt(process.env.DB_PORT || '5432'),
  ssl: { rejectUnauthorized: false }
});

async function checkDb() {
  try {
    const res = await pool.query(`
      SELECT column_name, data_type 
      FROM information_schema.columns 
      WHERE table_name = 'chat_history'
    `);
    console.log('Columns:');
    console.table(res.rows);

    const records = await pool.query('SELECT * FROM chat_history ORDER BY id DESC LIMIT 5');
    console.log('Last 5 records:');
    console.log(JSON.stringify(records.rows, null, 2));

    await pool.end();
  } catch (err) {
    console.error(err);
    process.exit(1);
  }
}

checkDb();
