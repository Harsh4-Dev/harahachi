// Interactive drag-to-dismiss for bottom sheets / full-height overlays (station
// panels, Annai chat, Checkpoint keeper). Returns a ref to attach to the sheet.
//
// Why a rewrite (Android real-touch): Pointer events on the Android WebView stop
// being delivered to JS the moment the browser decides the gesture is a native
// pan (touch-action / overscroll claim it). This uses TOUCH events with a
// non-passive touchmove + preventDefault so we keep 1:1 control, and it forces
// touch-action:none on the grab handles so the WebView never steals the gesture.
//
// Behaviour (user requirement):
//   • Finger-down on the header/grip ([data-swipe-handle]) OR in the scroll body
//     ([data-swipe-scroll]) while scrollTop===0 arms a drag.
//   • Vertical: the sheet's translateY FOLLOWS the finger 1:1 (no transition
//     during the drag, will-change:transform). On release:
//        – fast downward flick (velocity > 0.5 px/ms)         → close
//        – slow drag past 35% of the panel height             → close
//        – otherwise                                          → spring back (220ms)
//   • Horizontal (only when the gesture began on the header/grip and onNavigate
//     is provided): a ≥60px horizontal swipe with |dx| > 2|dy| navigates to the
//     adjacent station (swipe left → next, right → previous). Inner horizontal
//     scrollers live in the body, so they are never hijacked.
//
// The ✕ button and any button/input/tab are always left untouched.

import { useEffect, useRef } from 'react';

const CLOSE_MS = 260; // slide-out on close
const SPRING_MS = 220; // spring-back on cancel
const DISTANCE_FRAC = 0.35; // close if dragged past 35% of panel height
const FLICK_VEL = 0.5; // px/ms downward at release → close regardless of distance
const H_THRESHOLD = 60; // px horizontal to switch station
const SLOP = 8; // px of travel before a drag commits (taps still register)

export function useSwipeDown<T extends HTMLElement>(
  onClose: () => void,
  onNavigate?: (dir: 1 | -1) => void,
): React.RefObject<T> {
  const ref = useRef<T>(null);
  const cb = useRef({ onClose, onNavigate });
  cb.current = { onClose, onNavigate };

  useEffect(() => {
    const sheet = ref.current;
    if (!sheet) return;

    const scrollEl = () => sheet.querySelector<HTMLElement>('[data-swipe-scroll]');

    // Force the grab handles to swallow native gestures so the WebView hands us
    // every move event (the crux of the Android failure). CSS stays untouched.
    const handles = Array.from(sheet.querySelectorAll<HTMLElement>('[data-swipe-handle]'));
    handles.forEach((h) => {
      h.style.touchAction = 'none';
    });

    let startX = 0,
      startY = 0;
    let lastY = 0,
      lastT = 0,
      vel = 0; // px/ms (downward positive)
    let onHandle = false; // gesture began on header/grip
    let mode: 'none' | 'v' | 'h' = 'none';
    let armed = false;
    let closing = false;

    const height = () => sheet.getBoundingClientRect().height || window.innerHeight;

    const setTransition = (t: string) => {
      sheet.style.transition = t;
    };

    const settle = (animate: boolean) => {
      setTransition(animate ? `transform ${SPRING_MS}ms ease-out` : 'none');
      sheet.style.transform = '';
      mode = 'none';
      armed = false;
    };

    const doClose = (flick: boolean) => {
      if (closing) return;
      closing = true;
      const h = height();
      setTransition(`transform ${flick ? Math.round(CLOSE_MS * 0.72) : CLOSE_MS}ms ease-in`);
      sheet.style.transform = `translateY(${h}px)`;
      const done = () => {
        sheet.removeEventListener('transitionend', done);
        cb.current.onClose();
      };
      sheet.addEventListener('transitionend', done);
      // Fallback in case transitionend is dropped (WebView occasionally does).
      window.setTimeout(() => cb.current.onClose(), CLOSE_MS + 60);
    };

    const begin = (x: number, y: number, target: EventTarget | null) => {
      if (closing) return;
      const el = target as HTMLElement | null;
      if (!el) return;
      // Never hijack interactive controls (buttons, inputs, ✕, tabs, links).
      if (el.closest('button, input, textarea, select, a, [role="tab"]')) return;
      onHandle = !!el.closest('[data-swipe-handle]');
      const sc = scrollEl();
      const inScroll = sc ? sc.contains(el) : false;
      const atTop = sc ? sc.scrollTop <= 0 : true;
      armed = onHandle || (inScroll && atTop);
      if (!armed) return;
      startX = x;
      startY = y;
      lastY = y;
      lastT = performance.now();
      vel = 0;
      mode = 'none';
    };

    // Returns true when it consumed the move (caller should preventDefault).
    const move = (x: number, y: number, target: EventTarget | null): boolean => {
      if (!armed || closing) return false;
      const dx = x - startX;
      const dy = y - startY;

      if (mode === 'none') {
        if (onHandle && cb.current.onNavigate && Math.abs(dx) > SLOP && Math.abs(dx) > 2 * Math.abs(dy)) {
          mode = 'h';
          setTransition('none');
        } else if (dy > SLOP) {
          // Committing to a downward drag — re-check the scroll position so a
          // mid-scroll body drag never steals the content.
          const sc = scrollEl();
          const el = target as Node | null;
          if (sc && el && sc.contains(el) && sc.scrollTop > 0) {
            armed = false;
            return false;
          }
          mode = 'v';
          setTransition('none');
        } else if (dy < -SLOP) {
          // Upward — not our gesture; let the scroll container have it.
          armed = false;
          return false;
        } else {
          return false;
        }
      }

      const now = performance.now();
      if (mode === 'v') {
        const dt = now - lastT;
        if (dt > 0) vel = (y - lastY) / dt;
        lastY = y;
        lastT = now;
        const ty = Math.max(0, dy);
        sheet.style.transform = `translateY(${ty}px)`;
        return true;
      }
      if (mode === 'h') {
        // Subtle 1:2 follow for feedback; the real transition is the map pan +
        // panel remount that onNavigate triggers.
        const tx = Math.max(-90, Math.min(90, dx)) * 0.5;
        sheet.style.transform = `translateX(${tx}px)`;
        return true;
      }
      return false;
    };

    const end = (x: number, y: number) => {
      if (!armed || closing) {
        if (!closing) settle(false);
        return;
      }
      const dx = x - startX;
      const dy = y - startY;
      if (mode === 'v') {
        if (vel > FLICK_VEL || dy > DISTANCE_FRAC * height()) {
          doClose(vel > FLICK_VEL);
        } else {
          settle(true);
        }
      } else if (mode === 'h') {
        if (Math.abs(dx) > H_THRESHOLD && Math.abs(dx) > 2 * Math.abs(dy) && cb.current.onNavigate) {
          settle(true);
          cb.current.onNavigate(dx < 0 ? 1 : -1); // swipe left → next
        } else {
          settle(true);
        }
      } else {
        settle(false);
      }
      mode = 'none';
      armed = false;
    };

    // ---- Touch (primary path for the phone) ----
    const onTouchStart = (e: TouchEvent) => {
      if (e.touches.length !== 1) return;
      const t = e.touches[0];
      begin(t.clientX, t.clientY, e.target);
    };
    const onTouchMove = (e: TouchEvent) => {
      if (e.touches.length !== 1) return;
      const t = e.touches[0];
      if (move(t.clientX, t.clientY, e.target) && e.cancelable) e.preventDefault();
    };
    const onTouchEnd = (e: TouchEvent) => {
      const t = e.changedTouches[0];
      if (t) end(t.clientX, t.clientY);
      else settle(false);
    };

    // ---- Mouse (desktop dev fallback) ----
    let mouseDown = false;
    const onMouseDown = (e: MouseEvent) => {
      if (e.button !== 0) return;
      mouseDown = true;
      begin(e.clientX, e.clientY, e.target);
    };
    const onMouseMove = (e: MouseEvent) => {
      if (!mouseDown) return;
      if (move(e.clientX, e.clientY, e.target)) e.preventDefault();
    };
    const onMouseUp = (e: MouseEvent) => {
      if (!mouseDown) return;
      mouseDown = false;
      end(e.clientX, e.clientY);
    };

    sheet.addEventListener('touchstart', onTouchStart, { passive: true });
    window.addEventListener('touchmove', onTouchMove, { passive: false });
    window.addEventListener('touchend', onTouchEnd);
    window.addEventListener('touchcancel', onTouchEnd);
    sheet.addEventListener('mousedown', onMouseDown);
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);

    return () => {
      sheet.removeEventListener('touchstart', onTouchStart);
      window.removeEventListener('touchmove', onTouchMove);
      window.removeEventListener('touchend', onTouchEnd);
      window.removeEventListener('touchcancel', onTouchEnd);
      sheet.removeEventListener('mousedown', onMouseDown);
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
    };
  }, []);

  return ref;
}
