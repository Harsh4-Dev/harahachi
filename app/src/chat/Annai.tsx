// Annai chat (案内人) — DESIGN.md 6.6. A floating glass button (bottom-right,
// above the map and inside panels) opens a full-height glass overlay: washi
// message bubbles (user right, Annai left with a tiny hanko avatar), suggestion
// chips, a glass input with a --shu send button. POST /chat. When the reply
// carries an executed action, an inline confirmation chip renders with the stamp
// animation and the parent refreshes today's data live (journey bar advances).
//
// Failure UX: a failed send marks the user bubble tap-to-retry.

import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useApp } from '../state/AppContext';
import { ApiError } from '../api/http';
import { AI_COPY, classifyApiError } from '../api/errors';
import { useSwipeDown } from '../lib/useSwipeDown';
import type { ChatAction, ChatMessage, MealInput } from '../api/types';
import { fmtInt } from '../lib/format';

interface Bubble {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  action?: ChatAction | null;
  // 'proposed' → the server/demo suggested (did NOT log) → show a confirm chip
  // that logs only on tap. 'confirmed' → already logged (server executed it, or
  // the user tapped stamp) → show the stamped receipt chip. 'dismissed' → gone.
  actionState?: 'proposed' | 'confirmed' | 'dismissed';
  failed?: boolean; // user bubble whose send failed → tap to retry
  pending?: boolean; // assistant placeholder while waiting
}

const SUGGESTIONS = [
  "What's left today?",
  'Log my dinner',
  'What should I eat tonight?',
  'How does this app work?',
];

function actionSummary(a: ChatAction): string {
  switch (a.action) {
    case 'log_meal': {
      const dish = a.meal?.dish ?? a.dish ?? 'meal';
      const kcal = a.meal?.calories ?? a.calories ?? 0;
      return `Logged ${dish} · ${fmtInt(kcal)} kcal`;
    }
    case 'log_workout': {
      const kind = a.workout?.kind ?? a.kind ?? 'workout';
      const burned = a.workout?.calories_burned ?? a.calories_burned ?? 0;
      return `Logged ${kind} · ${fmtInt(burned)} kcal burned`;
    }
    case 'set_goal':
      return `Goal set to ${a.goal ?? ''}`;
    default:
      return 'Done';
  }
}

// A confirm-chip prompt for a PROPOSED (not-yet-logged) action.
function proposalSummary(a: ChatAction): string {
  switch (a.action) {
    case 'log_meal': {
      const dish = a.meal?.dish ?? a.dish ?? 'meal';
      const kcal = a.meal?.calories ?? a.calories ?? 0;
      return `Add ${dish} · ${fmtInt(kcal)} kcal to today?`;
    }
    case 'log_workout': {
      const kind = a.workout?.kind ?? a.kind ?? 'workout';
      const burned = a.workout?.calories_burned ?? a.calories_burned ?? 0;
      return `Log ${kind} · ${fmtInt(burned)} kcal burned?`;
    }
    case 'set_goal':
      return `Set your goal to ${a.goal ?? ''}?`;
    default:
      return 'Add this to today?';
  }
}

// The meal to POST when the user confirms a proposed log_meal action.
function mealFromAction(a: ChatAction): MealInput {
  return {
    dish: a.meal?.dish ?? a.dish ?? 'meal',
    calories: a.meal?.calories ?? a.calories ?? 0,
    protein_g: a.meal?.protein_g ?? a.protein_g ?? 0,
    carbs_g: a.meal?.carbs_g ?? a.carbs_g ?? 0,
    fat_g: a.meal?.fat_g ?? a.fat_g ?? 0,
    source: 'suggestion',
  };
}

export function AnnaiButton({ onDataChanged }: { onDataChanged: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        className="annai-fab glass-ink"
        aria-label="Open Annai, your road guide"
        onClick={() => setOpen(true)}
      >
        <span className="annai-torii">⛩</span>
      </button>
      {open && createPortal(<AnnaiOverlay onClose={() => setOpen(false)} onDataChanged={onDataChanged} />, document.body)}
    </>
  );
}

function AnnaiOverlay({ onClose, onDataChanged }: { onClose: () => void; onDataChanged: () => void }) {
  const { client, logMeal, toast } = useApp();
  const sheetRef = useSwipeDown<HTMLDivElement>(onClose);
  const [bubbles, setBubbles] = useState<Bubble[]>([
    {
      id: 0,
      role: 'assistant',
      content: "I'm Annai, your road guide. Ask what's left today, tell me what you ate, or ask what to eat tonight.",
    },
  ]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const nextId = useRef(1);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [bubbles]);

  const send = async (text: string, retryOfId?: number) => {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    setInput('');

    // History from prior non-failed bubbles for context.
    const history: ChatMessage[] = bubbles
      .filter((b) => !b.failed && !b.pending)
      .map((b) => ({ role: b.role, content: b.content }));

    // Add/replace the user bubble.
    let userId = retryOfId;
    setBubbles((prev) => {
      const cleared = retryOfId != null ? prev.map((b) => (b.id === retryOfId ? { ...b, failed: false } : b)) : prev;
      if (retryOfId != null) return cleared;
      userId = nextId.current++;
      return [...cleared, { id: userId, role: 'user', content: trimmed }];
    });

    const pendingId = nextId.current++;
    setBubbles((prev) => [...prev, { id: pendingId, role: 'assistant', content: '', pending: true }]);
    setBusy(true);

    try {
      const res = await client.chat([...history, { role: 'user', content: trimmed }]);
      // A PROPOSED action (server marks action.proposed, e.g. "what should I
      // eat?") is NOT logged — render a confirm chip and log only on tap.
      // An executed action (user said "I ate X") is already logged server-side.
      const proposed = res.action?.proposed === true;
      setBubbles((prev) =>
        prev.map((b) =>
          b.id === pendingId
            ? {
                ...b,
                content: res.reply,
                action: res.action,
                actionState: res.action ? (proposed ? 'proposed' : 'confirmed') : undefined,
                pending: false,
              }
            : b,
        ),
      );
      if (res.action && !proposed) onDataChanged(); // executed → journey bar advances live
    } catch (e) {
      // Remove the pending assistant bubble; mark the user bubble tap-to-retry.
      const net = e instanceof ApiError && e.status === 0;
      const kind = classifyApiError(e);
      setBubbles((prev) =>
        prev
          .filter((b) => b.id !== pendingId)
          .map((b) => (b.id === userId ? { ...b, failed: true } : b)),
      );
      // Surface a small inline hint bubble.
      setBubbles((prev) => [
        ...prev,
        {
          id: nextId.current++,
          role: 'assistant',
          content: net
            ? 'Sekisho is unreachable — tap your message to retry.'
            : kind === 'daily_cap'
              ? AI_COPY.daily_cap
              : kind === 'rate_limited' || kind === 'unavailable'
                ? 'I can’t answer just now — tap your message to retry in a moment.'
                : 'Something went wrong — tap your message to retry.',
        },
      ]);
    } finally {
      setBusy(false);
    }
  };

  // Confirm a proposed action → log it now, then show the stamped receipt chip.
  const confirmAction = async (bubbleId: number, action: ChatAction) => {
    if ('vibrate' in navigator) navigator.vibrate?.(30);
    try {
      if (action.action === 'log_meal') {
        await logMeal(mealFromAction(action));
      } else if (action.action === 'log_workout') {
        await client.logWorkout({
          kind: action.workout?.kind ?? action.kind ?? 'workout',
          minutes: action.workout?.minutes ?? action.minutes ?? 0,
          calories_burned: action.workout?.calories_burned ?? action.calories_burned ?? 0,
        });
      }
      setBubbles((prev) => prev.map((b) => (b.id === bubbleId ? { ...b, actionState: 'confirmed' } : b)));
      onDataChanged(); // now it's logged → journey bar advances
    } catch {
      toast('Could not add that — try again.', 'warn');
    }
  };

  const dismissAction = (bubbleId: number) => {
    setBubbles((prev) => prev.map((b) => (b.id === bubbleId ? { ...b, actionState: 'dismissed' } : b)));
  };

  return (
    <div className="annai-overlay" role="dialog" aria-label="Annai chat">
      <div className="annai-sheet glass" ref={sheetRef}>
        <div className="annai-head" data-swipe-handle>
          <div className="annai-grip" aria-hidden />
          <div className="annai-head-title">
            <span className="annai-avatar">案</span>
            <div>
              <div className="display" style={{ fontSize: 18 }}>Annai 案内人</div>
              <div className="small muted">your road guide</div>
            </div>
          </div>
          <button className="capture-x" aria-label="Close chat" onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="annai-scroll" ref={scrollRef} data-swipe-scroll>
          {bubbles.map((b) => (
            <div key={b.id} className={`annai-row annai-row-${b.role}`}>
              {b.role === 'assistant' && <span className="annai-mini-avatar">案</span>}
              <div className="annai-bubble-wrap">
                <button
                  className={`annai-bubble annai-bubble-${b.role} ${b.failed ? 'failed' : ''}`}
                  onClick={() => b.failed && void send(b.content, b.id)}
                  disabled={!b.failed}
                  style={{ cursor: b.failed ? 'pointer' : 'default' }}
                >
                  {b.pending ? <span className="annai-typing"><i /><i /><i /></span> : b.content}
                  {b.failed && <span className="annai-retry small"> · tap to retry</span>}
                </button>
                {b.action && b.actionState === 'proposed' && (
                  <div className="annai-confirm-chip glass">
                    <span className="annai-confirm-text">{proposalSummary(b.action)}</span>
                    <div className="annai-confirm-actions">
                      <button
                        className="annai-confirm-yes"
                        onClick={() => void confirmAction(b.id, b.action!)}
                      >
                        <span className="annai-action-seal">記</span> stamp
                      </button>
                      <button className="annai-confirm-no" onClick={() => dismissAction(b.id)}>
                        dismiss
                      </button>
                    </div>
                  </div>
                )}
                {b.action && b.actionState === 'confirmed' && (
                  <div className="annai-action-chip stamped">
                    <span className="annai-action-seal">記</span>
                    {actionSummary(b.action)}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>

        <div className="annai-suggestions">
          {SUGGESTIONS.map((s) => (
            <button key={s} className="annai-sug glass" onClick={() => void send(s)} disabled={busy}>
              {s}
            </button>
          ))}
        </div>

        <div className="annai-input-bar glass">
          <input
            className="annai-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void send(input);
            }}
            placeholder="Tell Annai what you ate…"
            aria-label="Message Annai"
          />
          <button className="annai-send" aria-label="Send" onClick={() => void send(input)} disabled={busy || !input.trim()}>
            ➤
          </button>
        </div>
      </div>
    </div>
  );
}
