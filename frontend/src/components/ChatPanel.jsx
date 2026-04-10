import React, { useState, useEffect, useRef } from 'react';

const ChatPanel = ({ activePo, messages, onSendMessage, isTyping }) => {
  const [inputValue, setInputValue] = useState('');
  const scrollRef = useRef(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages, isTyping]);

  const handleSend = () => {
    if (inputValue.trim()) {
      onSendMessage(inputValue);
      setInputValue('');
    }
  };

  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const showQuickReplies = messages.length > 0 && messages[0].sender_type === 'bot' && !messages.some(m => m.sender_type === 'vendor');

  const quickReplies = [
    { label: '✅ Full supply — confirming', value: 'I confirm full supply for this PO.' },
    { label: '⚠️ Partial supply — discuss', value: 'I can only fulfill partial supply. Let\'s discuss.' },
    { label: '❌ Cannot fulfill order', value: 'I cannot fulfill this order at this time.' }
  ];

  // Format date to DD/MM/YYYY
  const formatDeliveryDate = (dateStr) => {
    if (!dateStr) return '---';
    try {
      const date = new Date(dateStr);
      const day = String(date.getDate()).padStart(2, '0');
      const month = String(date.getMonth() + 1).padStart(2, '0');
      const year = date.getFullYear();
      return `${day}/${month}/${year}`;
    } catch (e) {
      return dateStr;
    }
  };

  // Generate the dynamic initial bot follow-up message
  const initialBotMessage = {
    sender_type: 'bot',
    message_text: `Hi there! 👋 I'm your Compass procurement assistant.\n\nI see you have Order #${activePo.po_id} scheduled for delivery on ${formatDeliveryDate(activePo.delivery_date)}.\n\nWill you be able to deliver this order on time? ✅`,
    sent_at: activePo.delivery_date || new Date().toISOString(),
    isInitial: true
  };

  // Prepend the initial bot message if it's not already in the fetched history
  const displayMessages = [initialBotMessage, ...messages];

  const handleRestart = async () => {
    try {
      await fetch(`http://localhost:5001/api/chat-history?po_id=${activePo.po_id}`, { method: 'DELETE' });
      window.location.reload();
    } catch (e) {
      console.error('Failed to restart conversation:', e);
    }
  };

  return (
    <div className="flex-1 flex flex-col h-screen bg-[#f1f5f9]">
      {/* Header */}
      <header className="bg-white border-b border-slate-200 px-8 py-6 shrink-0 shadow-sm">
        <div className="flex justify-between items-center mb-4">
          <div>
            <h2 className="text-xl font-bold text-navy-900">{activePo.po_id}</h2>
            <p className="text-slate-500 font-medium text-sm">{activePo.supplier_name}</p>
          </div>
          <div className="flex items-center gap-3">
            <button 
              onClick={handleRestart}
              className="px-3 py-1 text-xs font-bold bg-slate-100 hover:bg-slate-200 text-slate-600 rounded-md transition-colors"
            >
              🔄 Restart Chat
            </button>
            <div className="flex items-center gap-2 px-3 py-1 bg-green-50 rounded-full border border-green-200">
              <span className="w-2 h-2 rounded-full bg-accent-green pulse-green"></span>
              <span className="text-[11px] font-bold text-accent-green uppercase tracking-wider">Live</span>
            </div>
          </div>
        </div>
        
        <div className="flex items-center text-[12px] text-slate-500 gap-6 font-medium">
          <span className="bg-slate-100 px-2 py-0.5 rounded italic">Delivery: <span className="text-navy-900 not-italic font-bold">{formatDeliveryDate(activePo.delivery_date)}</span></span>
        </div>
      </header>

      {/* Message Thread */}
      <div className="flex-1 overflow-hidden relative flex flex-col">
        <div className="flex-1 overflow-y-auto p-8 flex flex-col gap-8 custom-scrollbar pb-20">
          
          {displayMessages.map((msg, i) => {
            const isBot = msg.sender_type === 'bot';
            const isOperator = msg.sender_type === 'operator';
            const isSystem = msg.sender_type === 'system';
            
            // Hide system messages (like "Operator took over") from the vendor view
            if (isSystem) return null;

            // Treat Bot and Operator messages identically as "Compass"
            const isCompass = isBot || isOperator;

            return (
              <div key={i} className={`flex flex-col ${isCompass ? 'items-start' : 'items-end'}`}>
                <div className={`max-w-[85%] px-4 py-2.5 rounded-2xl shadow-sm relative ${
                  isCompass 
                    ? 'bg-white text-slate-800 rounded-tl-none border border-slate-100' 
                    : 'bg-accent-green text-white rounded-tr-none'
                }`}>
                  <div className="flex flex-wrap items-end justify-end gap-x-4 gap-y-1">
                    <div className="text-[15px] leading-snug whitespace-pre-wrap font-medium flex-1 min-w-[80px]">
                      {msg.message_text}
                    </div>
                    <div className={`text-[10px] opacity-70 shrink-0 mb-[-2px] font-bold ${isCompass ? 'text-slate-500' : 'text-white'}`}>
                      {msg.isInitial ? 'Auto-Request' : new Date(msg.sent_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
          <div ref={scrollRef} />
        </div>

        {/* Fixed Bottom Left Typing Indicator */}
        {isTyping && (
          <div className="absolute bottom-4 left-8 animate-in fade-in slide-in-from-bottom-2 duration-300">
            <div className="bg-white/90 backdrop-blur-sm p-4 rounded-2xl rounded-tl-none shadow-md border border-slate-100 flex gap-3 items-center">
              <div className="flex gap-1.5 items-center">
                <span className="dot bg-accent-green"></span>
                <span className="dot bg-accent-green"></span>
                <span className="dot bg-accent-green"></span>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Footer / Input Area */}
      <footer className="p-8 bg-white border-t border-slate-200">
        <div className="flex gap-4 items-end bg-[#f8fafc] focus-within:bg-white p-3 rounded-2xl border border-slate-200 focus-within:border-accent-green/50 transition-all shadow-sm focus-within:shadow-md">
          <textarea
            className="flex-1 bg-transparent border-none focus:ring-0 outline-none text-lg font-medium resize-none max-h-32 py-1 placeholder:text-slate-400"
            placeholder="Type your message here..."
            rows={1}
            value={inputValue}
            disabled={isTyping}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyPress}
          />
          <button
            onClick={handleSend}
            disabled={!inputValue.trim() || isTyping}
            className="bg-accent-green text-white p-2.5 rounded-xl hover:bg-green-700 disabled:opacity-50 transition-all font-bold text-sm h-11 w-11 flex items-center justify-center shrink-0 shadow-md shadow-green-200"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/></svg>
          </button>
        </div>
      </footer>
    </div>
  );
};

export default ChatPanel;
