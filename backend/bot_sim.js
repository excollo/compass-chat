const axios = require('axios');

const API_MESSAGE = 'http://localhost:5000/api/chat-message';

const simulateBotResponse = async (po_id, text) => {
  try {
    const { data } = await axios.post(API_MESSAGE, {
      po_id,
      sender_type: 'bot',
      message_text: text
    });
    console.log(`Bot message sent for ${po_id}: "${text}"`);
    return data;
  } catch (err) {
    console.error('Failed to simulate bot response:', err.message);
  }
};

// If run from CLI: node bot_sim.js PO-2024-0341 "Message content"
const args = process.argv.slice(2);
if (args.length >= 2) {
  simulateBotResponse(args[0], args[1]);
} else {
  console.log('Usage: node bot_sim.js <po_id> "<message>"');
}

module.exports = { simulateBotResponse };
