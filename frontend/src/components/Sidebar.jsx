import React from 'react';

const Sidebar = ({ activePoId, onSelect, messages, poList }) => {
  // Group all POs by vendor name — one entry per vendor
  const vendorGroups = poList.reduce((acc, po) => {
    const name = po.supplier_name || 'Unknown Vendor';
    if (!acc[name]) {
      acc[name] = [];
    }
    acc[name].push(po);
    return acc;
  }, {});

  // For each vendor group, pick the most recent message across all their POs
  const vendorList = Object.entries(vendorGroups).map(([vendorName, pos]) => {
    // Find latest message across all POs for this vendor
    const allMessages = pos.flatMap(po => (messages[po.po_id] || []).filter(msg => msg.sender_type !== 'system'));
    const lastMsg = allMessages.sort(
      (a, b) => new Date(b.sent_at) - new Date(a.sent_at)
    )[0];
    const selectedPo = pos.find(po => po.po_id === lastMsg?.po_id) || pos[0];

    // Check if any of this vendor's POs is active
    const isActive = pos.some(po => po.po_id === activePoId);

    // PO count badge
    const poCount = pos.length;

    return { vendorName, selectedPo, lastMsg, isActive, poCount };
  });

  return (
    <div className="w-[300px] border-r border-slate-200 h-screen bg-navy-900 flex flex-col text-white">
      <div className="p-6 border-b border-white/10 shrink-0">
        <h1 className="text-xl font-bold tracking-tight">Suppliers Portal</h1>
        <p className="text-slate-400 text-xs mt-1">Live Procurement Hub</p>
      </div>

      <div className="flex-1 overflow-y-auto">
        {vendorList.map(({ vendorName, selectedPo, lastMsg, isActive, poCount }) => (
          <button
            key={vendorName}
            onClick={() => onSelect(selectedPo)}
            className={`w-full text-left p-4 transition-all border-l-4 ${
              isActive
                ? 'bg-slate-800 border-accent-green'
                : 'border-transparent hover:bg-slate-800/50'
            }`}
          >
            <div className="flex justify-between items-start mb-1">
              <span className="font-semibold text-sm truncate mr-2">
                {vendorName}
              </span>
              <span className="text-[10px] text-slate-400 uppercase font-medium shrink-0">
                {lastMsg
                  ? new Date(lastMsg.sent_at).toLocaleTimeString([], {
                      hour: '2-digit',
                      minute: '2-digit',
                    })
                  : '---'}
              </span>
            </div>

            {/* Show PO count badge when vendor has multiple POs */}
            {poCount > 1 && (
              <div className="text-[10px] text-slate-400 mb-1">
                {poCount} open POs
              </div>
            )}

            <div className="text-[11px] text-slate-400 italic truncate">
              {lastMsg ? lastMsg.message_text : 'No history'}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
};

export default Sidebar;