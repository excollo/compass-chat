import React, { useState, useEffect, useRef } from 'react';

const ChatPanel = ({ activePo, allPos = [], messages, onSendMessage, isTyping, isVendorMultiple }) => {
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

  // ─── Date separator label (WhatsApp style) ───────────────────────────────
  const getDateLabel = (dateStr) => {
    if (!dateStr) return null;
    const msgDate = new Date(dateStr);
    const today = new Date();
    const yesterday = new Date();
    yesterday.setDate(today.getDate() - 1);

    const isSameDay = (a, b) =>
      a.getDate() === b.getDate() &&
      a.getMonth() === b.getMonth() &&
      a.getFullYear() === b.getFullYear();

    if (isSameDay(msgDate, today)) return 'Today';
    if (isSameDay(msgDate, yesterday)) return 'Yesterday';

    return msgDate.toLocaleDateString('en-IN', {
      day: '2-digit',
      month: 'long',
      year: 'numeric',
    });
  };

  // ─── Build initial bot message ────────────────────────────────────────────
  // Single PO: mention it directly
  // Multiple POs: list all of them with delivery dates
  const buildInitialMessage = () => {
    const pos = allPos.length > 0 ? allPos : [activePo];

    if (pos.length === 1) {
      return (
        `Hi there! 👋 I'm your Compass procurement assistant.\n\n` +
        `I see you have Order #${pos[0].po_id} scheduled for delivery on ` +
        `${formatDeliveryDate(pos[0].delivery_date)}.\n\n` +
        `Will you be able to deliver this order on time? ✅`
      );
    }

    // Multiple POs — list each with its delivery date
    const poLines = pos
      .map(
        (po, idx) =>
          `${idx + 1}. Order #${po.po_id} — ${po.article_description || 'Items'} · Due ${formatDeliveryDate(po.delivery_date)}`
      )
      .join('\n');

    return (
      `Hi there! 👋 I'm your Compass procurement assistant.\n\n` +
      `I see you have ${pos.length} open orders scheduled this week:\n\n` +
      `${poLines}\n\n` +
      `Are all of these on track for delivery as scheduled? ✅`
    );
  };

  const initialBotMessage = {
    sender_type: 'bot',
    message_text: buildInitialMessage(),
    sent_at: new Date().toISOString(),
    isInitial: true,
  };

  const displayMessages = [initialBotMessage, ...messages];

  // ─── Inject date separators between messages ──────────────────────────────
  const messagesWithSeparators = [];
  let lastDateLabel = null;

  displayMessages.forEach((msg, i) => {
    if (msg.sender_type === 'system') return; // skip system messages

    const label = getDateLabel(msg.sent_at);
    if (label && label !== lastDateLabel) {
      messagesWithSeparators.push({ type: 'separator', label, key: `sep-${i}` });
      lastDateLabel = label;
    }
    messagesWithSeparators.push({ type: 'message', msg, key: `msg-${i}` });
  });

  // ─── Earliest delivery date for header ───────────────────────────────────
  const earliestDelivery = (allPos.length > 0 ? allPos : [activePo])
    .map(po => po.delivery_date)
    .filter(Boolean)
    .sort()[0];

  return (
    <div className="flex-1 flex flex-col h-screen bg-[#f1f5f9]">

      {/* Header */}
      <header className="bg-white border-b border-slate-200 px-8 py-6 shrink-0 shadow-sm">
        <div className="flex justify-between items-center mb-4">
          <div>
            <h2 className="text-xl font-bold text-navy-900">
              {activePo.supplier_name}
            </h2>
            {/* PO count badge when multiple */}
            {allPos.length > 1 && (
              <p className="text-slate-400 text-xs mt-0.5 font-medium">
                {allPos.length} open purchase orders
              </p>
            )}
          </div>
        </div>

        {/* Single PO: show one delivery date */}
        {allPos.length <= 1 && (
          <div className="flex items-center text-[12px] text-slate-500 gap-6 font-medium">
            <span className="bg-slate-100 px-2 py-0.5 rounded italic">
              Delivery:{' '}
              <span className="text-navy-900 not-italic font-bold">
                {formatDeliveryDate(activePo.delivery_date)}
              </span>
            </span>
          </div>
        )}

        {/* Multiple POs: show each PO as a small pill row */}
        {allPos.length > 1 && (
          <div className="flex flex-wrap gap-2 mt-2">
            {allPos.map(po => (
              <span
                key={po.po_id}
                className="text-[11px] bg-slate-100 text-slate-600 px-2.5 py-1 rounded-full font-medium border border-slate-200"
              >
                #{po.po_id} · {formatDeliveryDate(po.delivery_date)}
              </span>
            ))}
          </div>
        )}
      </header>

      {/* Message Thread */}
      <div className="flex-1 overflow-hidden relative flex flex-col">
        <div className="flex-1 overflow-y-auto p-8 flex flex-col gap-4 custom-scrollbar pb-20">

          {messagesWithSeparators.map((item) => {

            // ── Date separator ──────────────────────────────────────────────
            if (item.type === 'separator') {
              return (
                <div
                  key={item.key}
                  className="flex items-center gap-3 my-2"
                >
                  <div className="flex-1 h-px bg-slate-200" />
                  <span className="text-[11px] text-slate-400 font-semibold bg-slate-100 px-3 py-1 rounded-full border border-slate-200 shrink-0">
                    {item.label}
                  </span>
                  <div className="flex-1 h-px bg-slate-200" />
                </div>
              );
            }

            // ── Message bubble ──────────────────────────────────────────────
            const { msg } = item;
            const isBot = msg.sender_type === 'bot';
            const isOperator = msg.sender_type === 'operator';
            const isCompass = isBot || isOperator;

            return (
              <div
                key={item.key}
                className={`flex flex-col ${isCompass ? 'items-start' : 'items-end'}`}
              >
                {/* Sender label
                {isOperator && (
                  <span className="text-[10px] text-slate-400 mb-1 ml-1 font-semibold uppercase tracking-wide">
                    Operator
                  </span>
                )} */}

                <div
                  className={`max-w-[85%] px-4 py-2.5 rounded-2xl shadow-sm relative ${
                    isCompass
                      ? 'bg-white text-slate-800 rounded-tl-none border border-slate-100'
                      : 'bg-accent-green text-white rounded-tr-none'
                  }`}
                >
                  <div className="flex flex-wrap items-end justify-end gap-x-4 gap-y-1">
                    <div className="text-[15px] leading-snug whitespace-pre-wrap font-medium flex-1 min-w-[80px]">
                      {msg.message_text}
                    </div>
                    <div
                      className={`text-[10px] opacity-70 shrink-0 mb-[-2px] font-bold ${
                        isCompass ? 'text-slate-500' : 'text-white'
                      }`}
                    >
                      {msg.isInitial
                        ? 'Auto-Request'
                        : new Date(msg.sent_at).toLocaleTimeString([], {
                            hour: '2-digit',
                            minute: '2-digit',
                            hour12: false,
                          })}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}

          <div ref={scrollRef} />
        </div>

        {/* Typing indicator */}
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

      {/* Footer */}
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
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="m22 2-7 20-4-9-9-4Z" />
              <path d="M22 2 11 13" />
            </svg>
          </button>
        </div>
      </footer>
    </div>
  );
};

export default ChatPanel;