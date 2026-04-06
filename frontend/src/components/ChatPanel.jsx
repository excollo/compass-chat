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

  return (
    <div className="flex-1 flex flex-col h-screen bg-[#f1f5f9]">
      {/* Header */}
      <header className="bg-white border-b border-slate-200 px-8 py-6 shrink-0 shadow-sm">
        <div className="flex justify-between items-center mb-4">
          <div>
            <h2 className="text-xl font-bold text-navy-900">{activePo.po_id}</h2>
            <p className="text-slate-500 font-medium text-sm">{activePo.supplier_name}</p>
          </div>
          <div className="flex items-center gap-2 px-3 py-1 bg-green-50 rounded-full border border-green-200">
            <span className="w-2 h-2 rounded-full bg-accent-green pulse-green"></span>
            <span className="text-[11px] font-bold text-accent-green uppercase tracking-wider">Live</span>
          </div>
        </div>
        
        <div className="flex items-center text-[12px] text-slate-500 gap-6 font-medium">
          <span className="bg-slate-100 px-2 py-0.5 rounded italic">Delivery: <span className="text-navy-900 not-italic">{activePo.delivery_date}</span></span>
        </div>
      </header>

      {/* Message Thread */}
      <div className="flex-1 overflow-y-auto p-8 flex flex-col gap-6 custom-scrollbar">
        {messages.length === 0 && (
          <div className="text-center text-slate-400 mt-20 italic">No messages found for this PO.</div>
        )}
        
        {messages.map((msg, i) => {
          const isBot = msg.sender_type === 'bot';
          return (
            <div key={i} className={`flex flex-col ${isBot ? 'items-start' : 'items-end'}`}>
              <div className={`max-w-[70%] p-4 rounded-2xl shadow-sm ${
                isBot 
                  ? 'bg-white text-slate-800 rounded-tl-none border border-slate-100' 
                  : 'bg-accent-green text-white rounded-tr-none'
              }`}>
                <div className={`text-[10px] font-bold uppercase mb-2 ${isBot ? 'text-slate-400' : 'text-green-100'}`}>
                  {isBot ? 'Compass Bot' : 'Vendor'}
                </div>
                <div className="text-[14px] leading-relaxed whitespace-pre-wrap">{msg.message_text}</div>
                <div className={`text-[9px] mt-2 text-right ${isBot ? 'text-slate-400' : 'text-green-100'}`}>
                  {new Date(msg.sent_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                </div>
              </div>
            </div>
          );
        })}

        {isTyping && (
          <div className="flex justify-end">
            <div className="bg-white p-4 rounded-2xl rounded-tr-none shadow-sm border border-slate-100">
              <div className="flex gap-1 items-center">
                <span className="text-[10px] uppercase font-bold text-slate-400 mr-2">Bot Typing</span>
                <span className="dot"></span>
                <span className="dot"></span>
                <span className="dot"></span>
              </div>
            </div>
          </div>
        )}

        <div ref={scrollRef} />
      </div>

      {/* Footer / Input Area */}
      <footer className="p-8 bg-white border-t border-slate-200">
        {showQuickReplies && (
          <div className="flex gap-3 mb-6 flex-wrap">
            {quickReplies.map((reply, idx) => (
              <button
                key={idx}
                onClick={() => onSendMessage(reply.value)}
                className="bg-white border border-slate-200 text-slate-700 font-semibold px-4 py-2 rounded-full text-[12px] hover:border-accent-green hover:text-accent-green transition-all shadow-sm"
              >
                {reply.label}
              </button>
            ))}
          </div>
        )}

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
