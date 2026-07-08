// Swipe-down-to-dismiss for bottom sheets / full-height overlays (station
// panels, Annai chat, Checkpoint keeper). Returns a ref to attach to the sheet
// element; the ✕ button stays untouched.
//
// Behaviour (user requirement): dragging the sheet down past ~80px closes it
// with the same 280ms slide, otherwise it springs back. It must NOT fight the
// panel's own scrolling — a drag only starts when:
//   • the pointer went down on a grab-handle/header (mark it [data-swipe-handle]), OR
//   • the pointer went down inside the scroll container ([data-swipe-scroll])
//     while it is at scrollTop 0 AND the finger moves downward.
// Anything else (buttons, inputs, mid-scroll) is left alone.

import { useEffect, useRef } from 'react';

const THRESHOLD = 80; // px past which the sheet closes
const CLOSE_MS = 280; // matches the sheet-up open animation
const SPRING_MS = 220;

export function useSwipeDown<T extends HTMLElement>(onClose: () => void): React.RefObject<T> {
  const ref = useRef<T>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const sheet = ref.current;
    if (!sheet) return;

    let startY = 0;
    let dy = 0;
    let armed = false; // pointer landed somewhere a drag is permitted
    let dragging = false; // a real downward drag is underway
    let pointerId = -1;

    const scrollEl = () => sheet.querySelector<HTMLElement>('[data-swipe-scroll]');

    const reset = (animate: boolean) => {
      if (animate) {
        sheet.style.transition = `transform ${SPRING_MS}ms ease-out`;
        sheet.style.transform = '';
      } else {
        sheet.style.transition = '';
        sheet.style.transform = '';
      }
      dy = 0;
      armed = false;
      dragging = false;
      pointerId = -1;
    };

    const onDown = (e: PointerEvent) => {
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      const target = e.target as HTMLElement | null;
      if (!target) return;
      // Never hijack interactive controls (buttons, inputs, the ✕, tabs, links).
      if (target.closest('button, input, textarea, select, a, [role="tab"]')) return;

      const onHandle = !!target.closest('[data-swipe-handle]');
      const sc = scrollEl();
      const inScroll = sc ? sc.contains(target) : false;
      const atTop = sc ? sc.scrollTop <= 0 : true;

      armed = onHandle || (inScroll && atTop);
      if (!armed) return;
      startY = e.clientY;
      dy = 0;
      dragging = false;
      pointerId = e.pointerId;
    };

    const onMove = (e: PointerEvent) => {
      if (!armed || e.pointerId !== pointerId) return;
      const d = e.clientY - startY;
      if (d <= 0) {
        // Upward: not our gesture. If we hadn't committed, disarm so the scroll
        // container behaves normally.
        if (!dragging) armed = false;
        return;
      }
      if (!dragging) {
        if (d < 6) return; // ignore micro-jitter so taps still register
        // Re-check the scroll position at commit time: if the content scrolled
        // in the meantime, don't steal it.
        const sc = scrollEl();
        const tgt = e.target;
        if (sc && tgt instanceof Node && sc.contains(tgt) && sc.scrollTop > 0) {
          armed = false;
          return;
        }
        dragging = true;
        sheet.style.transition = 'none';
      }
      dy = d;
      sheet.style.transform = `translateY(${dy}px)`;
      // Prevent the scroll container from also panning during the drag.
      if (e.cancelable) e.preventDefault();
    };

    const onUp = (e: PointerEvent) => {
      if (e.pointerId !== pointerId && pointerId !== -1) return;
      if (dragging && dy > THRESHOLD) {
        const h = sheet.getBoundingClientRect().height || window.innerHeight;
        sheet.style.transition = `transform ${CLOSE_MS}ms ease-in`;
        sheet.style.transform = `translateY(${h}px)`;
        const done = () => {
          sheet.removeEventListener('transitionend', done);
          onCloseRef.current();
        };
        sheet.addEventListener('transitionend', done);
        // Fallback in case transitionend doesn't fire.
        window.setTimeout(() => onCloseRef.current(), CLOSE_MS + 40);
        armed = false;
        dragging = false;
        pointerId = -1;
      } else {
        reset(dragging);
      }
    };

    // touch-action:none on the handle keeps the browser from claiming the
    // vertical gesture; we set it via JS so the CSS stays untouched.
    sheet.addEventListener('pointerdown', onDown);
    window.addEventListener('pointermove', onMove, { passive: false });
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
    return () => {
      sheet.removeEventListener('pointerdown', onDown);
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
    };
  }, []);

  return ref;
}
