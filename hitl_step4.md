Prompt
Make the following changes to the vendor chat message rendering only.
Do not change any other files. Do not change how vendor or bot messages look.

Change 1 — Add operator message style
Find where messages are rendered in a list/map. Add a condition for
sender_type === 'operator':
Current structure likely has conditions for 'bot' and 'vendor'.
Add 'operator' as a new case:
jsx{/* Operator message — visually distinct from bot */}
{message.sender_type === 'operator' && (
  <div style={{
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'flex-start',
    marginBottom: '12px'
  }}>
    <div style={{
      background: '#1E3A5F',
      color: '#FFFFFF',
      borderRadius: '12px 12px 12px 0px',
      padding: '10px 14px',
      maxWidth: '75%'
    }}>
      <div style={{
        fontSize: '10px',
        fontWeight: 700,
        color: '#93C5FD',
        marginBottom: '5px',
        textTransform: 'uppercase',
        letterSpacing: '0.06em'
      }}>
        Compass Procurement Team
      </div>
      <div style={{ fontSize: '14px', lineHeight: '1.5' }}>
        {message.message_text}
      </div>
    </div>
    <span style={{ fontSize: '11px', color: '#9CA3AF', marginTop: '3px' }}>
      {new Date(message.sent_at).toLocaleTimeString('en-IN', {
        hour: '2-digit', minute: '2-digit'
      })}
    </span>
  </div>
)}

Change 2 — Add system message divider style
Add a condition for sender_type === 'system'.
System messages are events like "Bot paused" and "Bot resumed" —
they should show as a centered divider line, not a chat bubble:
jsx{message.sender_type === 'system' && (
  <div style={{
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    margin: '12px 0',
    padding: '0 8px'
  }}>
    <div style={{ flex: 1, height: '0.5px', background: '#E5E7EB' }} />
    <span style={{
      fontSize: '11px',
      color: '#9CA3AF',
      whiteSpace: 'nowrap',
      fontStyle: 'italic'
    }}>
      {message.message_text}
    </span>
    <div style={{ flex: 1, height: '0.5px', background: '#E5E7EB' }} />
  </div>
)}

Do NOT change

Bot message styling
Vendor message styling
WebSocket connection logic
Message input box
Any API calls or state management



Quick Reference — What each file does in HITL
FileRole in HITLSupabaseSource of truth — thread_state lives herebackend_agent/main.pyChecks thread_state before bot sends. Injects context on resume.backend/server.jsRoutes vendor messages to Python. Delivers operator messages via WebSocket.src/pages/Chats.jsxAdmin clicks Take Over / Hand Back. Sends operator messages.frontend/ vendor UIShows operator messages in dark blue. Shows system dividers.
Key values for thread_state
ValueMeaningbot_activeBot is running normallyhuman_controlledAdmin took over — bot is stoppedescalatedPaused and flagged — creates escalation caseresolvedConversation closed