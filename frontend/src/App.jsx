import React, { useState, useEffect, useCallback } from 'react';
import Sidebar from './components/Sidebar';
import ChatPanel from './components/ChatPanel';
import axios from 'axios';

// Vite environment variables (VITE_ prefix required)
const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:5000/api';
const WS_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:5000/ws/chat';

function App() {
  const [poList, setPoList] = useState([]);
  const [activePoId, setActivePoId] = useState(null);
  const [messages, setMessages] = useState({}); // po_id -> [messages]
  const [isTyping, setIsTyping] = useState({}); // po_id -> boolean
  const [ws, setWs] = useState(null);

  const activePo = poList.find(p => p.po_id === activePoId) || poList[0];

  // Fetch POs from DB
  const fetchPurchaseOrders = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API_BASE}/purchase-orders`);
      setPoList(data);
      if (data.length > 0 && !activePoId) {
        setActivePoId(data[0].po_id);
      }
    } catch (err) {
      console.error('Failed to fetch PO list from DB:', err);
    }
  }, [activePoId]);

  // Fetch history for a PO
  const fetchHistory = useCallback(async (po_id) => {
    try {
      const { data } = await axios.get(`${API_BASE}/chat-history?po_id=${po_id}`);
      setMessages(prev => ({ ...prev, [po_id]: data }));
    } catch (err) {
      console.error('Failed to fetch history:', err);
    }
  }, []);

  // Initialize WebSocket
  useEffect(() => {
    const socket = new WebSocket(WS_URL);
    
    socket.onopen = () => console.log('WebSocket Connected');
    socket.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.event === 'new_message') {
        const { po_id, sender_type, message_text, sent_at } = data;
        
        // Prevent duplicate messages if added optimistically
        setMessages(prev => {
          const currentMsgs = prev[po_id] || [];
          const exists = currentMsgs.find(m => m.message_text === message_text && m.sent_at === sent_at);
          if (exists) return prev;
          
          return {
            ...prev,
            [po_id]: [...currentMsgs, { sender_type, message_text, sent_at }]
          };
        });
        
        // Clear typing indicator if bot responded
        if (sender_type === 'bot') {
          setIsTyping(prev => ({ ...prev, [po_id]: false }));
        }
      }
    };
    
    socket.onclose = () => {
      console.log('WebSocket Disconnected, retrying...');
      setTimeout(() => setWs(new WebSocket(WS_URL)), 3000);
    };

    setWs(socket);
    return () => socket.close();
  }, []);

  // On mount: Fetch POs
  useEffect(() => {
    fetchPurchaseOrders();
  }, [fetchPurchaseOrders]);

  // On list change: Fetch all histories
  useEffect(() => {
    poList.forEach(po => fetchHistory(po.po_id));
  }, [poList, fetchHistory]);

  const handleSendMessage = async (text) => {
    try {
      if (!activePo) return;
      
      // Start typing indicator for bot
      setIsTyping(prev => ({ ...prev, [activePoId]: true }));

      // Save to backend (PostgreSQL + n8n trigger)
      await axios.post(`${API_BASE}/chat-message`, {
        po_id: activePoId,
        sender_type: 'vendor',
        message_text: text,
        vendor_phone: activePo.vendor_phone,
        supplier_name: activePo.supplier_name
      });

    } catch (err) {
      console.error('Failed to send message:', err);
      setIsTyping(prev => ({ ...prev, [activePoId]: false }));
    }
  };

  if (poList.length === 0) {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-[#0f172a] text-white">
        <div className="text-center">
          <h1 className="text-2xl font-bold mb-2">Connecting to Data Hub...</h1>
          <p className="text-slate-400">Verifying purchase order records.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-white">
      <Sidebar 
        activePoId={activePoId} 
        onSelect={(po) => setActivePoId(po.po_id)} 
        messages={messages} 
        poList={poList}
      />
      <ChatPanel 
        activePo={activePo} 
        messages={messages[activePoId] || []} 
        onSendMessage={handleSendMessage}
        isTyping={isTyping[activePoId]}
      />
    </div>
  );
}

export default App;
