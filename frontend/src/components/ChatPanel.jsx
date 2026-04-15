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

    if (isSameDay(msgDate, today)) return null;
    if (isSameDay(msgDate, yesterday)) return 'Yesterday';

    return msgDate.toLocaleDateString('en-IN', {
      day: '2-digit',
      month: 'long',
      year: 'numeric',
    });
  };

  const sortedMessages = [...messages].sort(
    (a, b) => new Date(a.sent_at || 0) - new Date(b.sent_at || 0)
  );

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

  // ─── Hardcoded reminder history for Yashoda Gas Service (PO 4100260367) ─────
  const YASHODA_PO_ID = '4100260367';
  const yashodaReminderMessages =
    activePo?.po_id === YASHODA_PO_ID
      ? [
          {
            sender_type: 'bot',
            message_text:
              'Day-7: Initial outreach message sent regarding PO 4100260367.',
            sent_at: '2026-04-07T09:00:00Z',
          },
          {
            sender_type: 'bot',
            message_text:
              'Day-5: First reminder — PO 4100260367 delivery is approaching. Please confirm status.',
            sent_at: '2026-04-09T10:30:00Z',
          },
          {
            sender_type: 'bot',
            message_text:
              "Day-3: Second reminder — PO 4100260367 is due in 3 days. We haven't heard from you.",
            sent_at: '2026-04-11T14:15:00Z',
          },
          {
            sender_type: 'bot',
            message_text:
              'Day-1: Final reminder — PO 4100260367 is due tomorrow. Please confirm IMMEDIATELY.',
            sent_at: '2026-04-13T16:45:00Z',
          },
        ]
      : [];

  const historyHasInitial = sortedMessages.some((m) => {
    if ((m.sender_type || '').toLowerCase().trim() !== 'bot') return false;
    return (
      (m.message_text || '').includes('Will you be able to deliver') ||
      (m.message_text || '').includes('I see you have Order') ||
      (m.message_text || '').includes('I see you have')
    );
  });

  const displayMessages = historyHasInitial
    ? [...yashodaReminderMessages, ...sortedMessages]
    : [initialBotMessage, ...yashodaReminderMessages, ...sortedMessages];

  // ─── Inject date separators between messages ──────────────────────────────
  const messagesWithSeparators = [];
  let lastDateLabel = null;

  displayMessages.forEach((msg, i) => {
    const label = getDateLabel(msg.sent_at);
    if (label && label !== lastDateLabel) {
      messagesWithSeparators.push({ type: 'separator', label, key: `sep-${i}` });
      lastDateLabel = label;
    }
    messagesWithSeparators.push({ type: 'message', msg, key: `msg-${i}` });
  });

  return (
    <div className="flex-1 flex flex-col h-screen bg-[#f8fafc]">

      {/* Header */}
      <header className="bg-white border-b border-slate-200 px-8 py-5 shrink-0 shadow-sm z-10">
        <div className="flex justify-between items-center">
          <div>
            <div className="flex items-center gap-3">
              <div>
                <h2 className="text-lg font-bold text-slate-900 leading-tight">
                  {activePo.supplier_name}
                </h2>
                <div className="flex items-center gap-2 mt-0.5">
                  <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
                  <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">
                    AI Assistant Active
                  </span>
                </div>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-4">
            {allPos.length <= 1 ? (
              <span className="bg-slate-50 text-slate-500 px-3 py-1.5 rounded-lg text-xs font-semibold border border-slate-100 italic">
                Delivery: <span className="text-slate-900 not-italic font-bold ml-1">{formatDeliveryDate(activePo.delivery_date)}</span>
              </span>
            ) : (
              <div className="flex flex-col items-end">
                <span className="text-[10px] font-bold text-slate-400 uppercase mb-1">Active Batch</span>
                <div className="flex gap-2">
                  {allPos.slice(0, 3).map(po => (
                    <span key={po.po_id} className="bg-slate-50 text-[10px] font-bold px-2 py-0.5 rounded border border-slate-100">
                      #{po.po_id}
                    </span>
                  ))}
                  {allPos.length > 3 && <span className="text-[10px] font-bold text-slate-400">+{allPos.length - 3}</span>}
                </div>
              </div>
            )}
          </div>
        </div>
      </header>

      {/* Chat messages */}
      <div className="flex-1 overflow-y-auto px-8 py-10 space-y-10 flex flex-col">
        <div className="flex-1">
          {messagesWithSeparators.map((item) => {
            if (item.type === 'separator') {
              return (
                <div key={item.key} className="flex items-center gap-4 my-10">
                  <div className="flex-1 h-px bg-slate-200" />
                  <span className="text-[11px] font-bold text-slate-400 uppercase tracking-widest bg-white px-4 py-1 rounded-full border border-slate-100 shadow-sm">
                    {item.label}
                  </span>
                  <div className="flex-1 h-px bg-slate-200" />
                </div>
              );
            }

            const { msg } = item;
            const senderType = (msg.sender_type || '').toLowerCase().trim();
            const isBot = senderType === 'bot';
            const isOperator = senderType === 'operator';
            const isSystem = senderType === 'system';
            const isCompass = isBot || isOperator;

            return (
              <div
                key={item.key}
                className={`flex w-full mb-2 ${
                  isSystem ? 'justify-center' : isCompass ? 'justify-start' : 'justify-end'
                }`}
              >
                {/* Message */}
                <div className={`flex flex-col ${isSystem ? 'max-w-[85%] items-center' : 'max-w-[75%]'}`}>
                  <div
                    className={`px-5 py-3.5 rounded-2xl shadow-sm relative group ${
                      isSystem
                        ? 'bg-amber-50 text-amber-800 border border-amber-100 rounded-xl'
                        : isCompass
                        ? 'bg-white text-slate-800 rounded-tl-none border border-slate-200'
                        : 'bg-[#0047cc] text-white rounded-tr-none'
                    }`}
                  >
                    <div className="text-[15px] leading-relaxed whitespace-pre-wrap font-medium break-words">
                      {msg.message_text}
                    </div>
                    
                    {/* Time */}
                    <div
                      className={`text-[9px] mt-1.5 font-bold tracking-tight uppercase opacity-50 ${
                        isSystem
                          ? 'text-amber-700'
                          : isCompass
                          ? 'text-slate-500'
                          : 'text-white'
                      }`}
                    >
                      {msg.isInitial
                        ? 'Auto-Request'
                        : new Date(msg.sent_at).toLocaleTimeString([], {
                            hour: '2-digit',
                            minute: '2-digit',
                            hour12: true,
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
          <div className="flex justify-start mt-2 animate-in fade-in slide-in-from-bottom-2 duration-300">
            <div className="bg-white/90 backdrop-blur-sm px-4 py-3 rounded-2xl rounded-tl-none shadow-md border border-slate-100 flex gap-3 items-center">
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