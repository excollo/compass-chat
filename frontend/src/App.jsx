import React, { useState, useEffect, useCallback } from 'react';
import Sidebar from './components/Sidebar';
import ChatPanel from './components/ChatPanel';
import axios from 'axios';

// Vite environment variables (VITE_ prefix required)
const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:5001/api';
const WS_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:5001';

function App() {
  const [poList, setPoList] = useState([]);
  const [activePoId, setActivePoId] = useState(() => {
    return localStorage.getItem('activePoId') || null;
  });
  const [messages, setMessages] = useState({});
  const [isTyping, setIsTyping] = useState({});
  const [threadStates, setThreadStates] = useState({});
  const [ws, setWs] = useState(null);

  // Derive activePo and allPos reactively — no extra state needed
  const activePo = poList.find(p => p.po_id === activePoId) || poList[0];
  const allPos = activePo
    ? poList.filter(p => p.supplier_name === activePo.supplier_name)
    : [];

  // Persist activePoId to localStorage whenever it changes
  useEffect(() => {
    if (activePoId) {
      localStorage.setItem('activePoId', activePoId);
    }
  }, [activePoId]);

  // Fetch POs from DB
  const fetchPurchaseOrders = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API_BASE}/purchase-orders`);
      setPoList(data);
      if (data.length > 0) {
        if (!activePoId) setActivePoId(data[0].po_id);

        // Initialize thread states from PO data
        const initialStates = {};
        data.forEach(po => {
          initialStates[po.po_id] = po.thread_state || 'bot_active';
        });
        setThreadStates(prev => ({ ...initialStates, ...prev }));
      }
    } catch (err) {
      console.error('Failed to fetch PO list from DB:', err);
    }
  }, [activePoId]);

  // Fetch history for a single PO
  const fetchHistory = useCallback(async (po_id) => {
    try {
      const { data } = await axios.get(`${API_BASE}/chat-history?po_id=${po_id}`);
      setMessages(prev => ({ ...prev, [po_id]: data || [] }));
    } catch (err) {
      console.error('Failed to fetch history:', err);
    }
  }, []);

  // Initialize WebSocket
  useEffect(() => {
    const socket = new WebSocket(WS_URL);

    socket.onopen = () => console.log('WebSocket Connected to ' + WS_URL);

    socket.onmessage = (event) => {
      const data = JSON.parse(event.data);

      if (data.event === 'new_message') {
        const { po_id, sender_type, message_text, sent_at } = data;

        setMessages(prev => {
          const currentMsgs = prev[po_id] || [];

          // Deduplicate — ignore if same message arrived within last 10 seconds
          const exists = currentMsgs.find(m =>
            m.message_text === message_text &&
            m.sender_type === sender_type &&
            (new Date(sent_at) - new Date(m.sent_at) < 10000)
          );

          if (exists) return prev;

          return {
            ...prev,
            [po_id]: [...currentMsgs, { sender_type, message_text, sent_at }]
          };
        });

        // Clear typing indicator when bot responds
        if (sender_type === 'bot') {
          setIsTyping(prev => ({ ...prev, [po_id]: false }));
        }
      }

      if (data.event === 'thread_state_change') {
        const { po_id, thread_state } = data;
        setThreadStates(prev => ({ ...prev, [po_id]: thread_state }));

        // Kill typing indicator immediately on human takeover
        if (thread_state === 'human_controlled') {
          setIsTyping(prev => ({ ...prev, [po_id]: false }));
        }
      }
    };

    socket.onclose = () => {
      console.log('WebSocket Disconnected, retrying...');
      setTimeout(() => {
        const newSocket = new WebSocket(WS_URL);
        setWs(newSocket);
      }, 3000);
    };

    setWs(socket);
    return () => socket.close();
  }, []);

  // On mount: fetch PO list
  useEffect(() => {
    fetchPurchaseOrders();
  }, [fetchPurchaseOrders]);

  // When PO list loads: fetch chat history for every PO
  useEffect(() => {
    poList.forEach(po => fetchHistory(po.po_id));
  }, [poList, fetchHistory]);

  const handleSendMessage = async (text) => {
    if (!activePo || !activePoId) return;

    const sent_at = new Date().toISOString();

    // Optimistic UI — show vendor message immediately
    setMessages(prev => ({
      ...prev,
      [activePoId]: [
        ...(prev[activePoId] || []),
        { sender_type: 'vendor', message_text: text, sent_at }
      ]
    }));

    try {
      const currentState = threadStates[activePoId] || 'bot_active';

      if (currentState === 'bot_active') {
        setIsTyping(prev => ({ ...prev, [activePoId]: true }));
      } else {
        setIsTyping(prev => ({ ...prev, [activePoId]: false }));
      }

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
        allPos={allPos}
        messages={messages[activePoId] || []}
        onSendMessage={handleSendMessage}
        isTyping={isTyping[activePoId]}
        isVendorMultiple={allPos.length > 1}
      />
    </div>
  );
}

export default App;