import React from 'react';

const Sidebar = ({ activePoId, onSelect, messages, poList }) => {
  const getStatusColor = (status) => {
    switch (status) {
      case 'Confirmed': return 'bg-green-100 text-green-700 border-green-200';
      case 'Awaiting Reply': return 'bg-yellow-100 text-yellow-700 border-yellow-200';
      case 'Exception': return 'bg-red-100 text-red-700 border-red-200';
      default: return 'bg-slate-100 text-slate-700 border-slate-200';
    }
  };

  const getStatusText = (po) => {
    return po.status === 'pending' ? 'Awaiting Reply' : po.status;
  };

  return (
    <div className="w-[300px] border-r border-slate-200 h-screen bg-navy-900 flex flex-col text-white">
      <div className="p-6 border-b border-white/10 shrink-0">
        <h1 className="text-xl font-bold tracking-tight">Suppliers Portal</h1>
        <p className="text-slate-400 text-xs mt-1">Live Procurement Hub</p>
      </div>

      <div className="flex-1 overflow-y-auto">
        {poList.map((po) => {
          const isActive = activePoId === po.po_id;
          const poMessages = messages[po.po_id] || [];
          const lastMsg = poMessages[poMessages.length - 1];
          const status = getStatusText(po);

          return (
            <button
              key={po.po_id}
              onClick={() => onSelect(po)}
              className={`w-full text-left p-4 transition-all border-l-4 ${
                isActive 
                  ? 'bg-slate-800 border-accent-green' 
                  : 'border-transparent hover:bg-slate-800/50'
              }`}
            >
              <div className="flex justify-between items-start mb-1">
                <span className="font-semibold text-sm">{po.po_id}</span>
                <span className="text-[10px] text-slate-400 uppercase font-medium">
                  {lastMsg ? new Date(lastMsg.sent_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '---'}
                </span>
              </div>
              <div className="text-xs text-slate-300 mb-2 font-medium truncate">{po.supplier_name}</div>
              
              <div className="flex justify-between items-center">
                <span className={`text-[10px] px-2 py-0.5 rounded-full border ${getStatusColor(status)}`}>
                  {status}
                </span>
                <div className="text-[11px] text-slate-400 italic flex-1 ml-3 truncate">
                  {lastMsg ? lastMsg.message_text : 'No history'}
                </div>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
};

export default Sidebar;
