// "Estimate body fat from a photo" (DESIGN.md 6.6): full-screen camera in the
// capture style (reuses useCamera), full-body capture → POST /profile/body-photo
// → an EDITABLE glass result screen (estimated %, adjustable range slider,
// confidence note, "photo analyzed and discarded" line) → confirm returns the
// chosen % to the parent (which saves via PUT /profile).
//
// Failure UX: analyze failure → the shared error popup with "Retry / Pick from
// diagram" (manual = cancel back to the tile picker); the daily AI cap offers
// only the diagram. Timeouts live in the client.
//
// v4: the photo is downscaled (lib/imagePrep, ≤1024px, ≤170 KB, EXIF-upright)
// before upload; the full-res frame is only used for the on-screen preview.

import { useCallback, useEffect, useRef, useState } from 'react';
import { useCamera, type Frame } from '../capture/useCamera';
import { useApp } from '../state/AppContext';
import { AI_COPY, classifyApiError } from '../api/errors';
import { prepareImageForUpload, type PrepOptions } from '../lib/imagePrep';
import { PetalLoader } from '../components/PetalLoader';
import type { BodyPhotoEstimate } from '../api/types';

interface Props {
  onClose: () => void; // cancel / "enter manually"
  onConfirm: (pct: number) => void;
  reducedMotion: boolean;
}

export function BodyPhotoCapture({ onClose, onConfirm, reducedMotion }: Props) {
  const { client, showError } = useApp();
  const { videoRef, status, captureFrame } = useCamera(true);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const [frame, setFrame] = useState<Frame | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [estimate, setEstimate] = useState<BodyPhotoEstimate | null>(null);
  const [pct, setPct] = useState(20);

  const noCamera = status === 'denied' || status === 'unsupported';

  const revoke = useCallback((f: Frame | null) => {
    if (f) URL.revokeObjectURL(f.url);
  }, []);

  const runEstimate = useCallback(
    async (f: Frame, prep?: PrepOptions) => {
      setFrame((prev) => {
        revoke(prev);
        return f;
      });
      setEstimate(null);
      setAnalyzing(true);
      try {
        let upload: Blob;
        try {
          upload = (await prepareImageForUpload(f.blob, prep)).blob;
        } catch {
          upload = f.blob; // undecodable here — the server re-encodes or 413s
        }
        const r = await client.estimateBodyPhoto(upload);
        setEstimate(r);
        setPct(Math.round(r.body_fat_pct));
      } catch (e) {
        // Reset to the live feed and surface a retry/manual choice.
        revoke(f);
        setFrame(null);
        setEstimate(null);
        const kind = classifyApiError(e);
        if (kind === 'device_revoked' || kind === 'no_user') return; // app is re-routing
        if (kind === 'daily_cap') {
          showError({ message: AI_COPY.daily_cap, secondaryLabel: 'Pick from diagram', onSecondary: onClose });
          return;
        }
        const again = (p?: PrepOptions) => () => void runEstimate({ ...f, url: URL.createObjectURL(f.blob) }, p);
        showError({
          message:
            kind === 'too_large' || kind === 'network' || kind === 'rate_limited'
              ? AI_COPY[kind]
              : kind === 'unavailable'
                ? 'The photo reader is resting right now — try again, or pick from the diagram.'
                : 'Could not read the photo.',
          retry: kind === 'too_large' ? again({ maxEdge: 768, maxBytes: 100 * 1024 }) : again(prep),
          secondaryLabel: 'Pick from diagram',
          onSecondary: onClose,
        });
      } finally {
        setAnalyzing(false);
      }
    },
    [client, revoke, showError, onClose],
  );

  const [failure, setFailure] = useState<string | null>(null);

  const onShutter = useCallback(async () => {
    if (analyzing || estimate) return;
    const f = await captureFrame();
    if (!f) {
      setFailure('Camera not ready — use the gallery button.');
      return;
    }
    setFailure(null);
    void runEstimate(f);
  }, [analyzing, estimate, captureFrame, runEstimate]);

  const onGallery = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      e.target.value = '';
      if (!file) return;
      const f: Frame = { blob: file, url: URL.createObjectURL(file), width: 0, height: 0 };
      setFailure(null);
      void runEstimate(f);
    },
    [runEstimate],
  );

  useEffect(() => () => revoke(frame), [frame, revoke]);

  const range = estimate?.range ?? [Math.max(6, pct - 5), Math.min(45, pct + 5)];

  return (
    <div className="capture-full" role="dialog" aria-label="Body-fat photo estimate">
      {frame ? (
        <img className="capture-feed" src={frame.url} alt="Captured body photo" />
      ) : (
        <video
          ref={videoRef}
          className="capture-feed"
          muted
          playsInline
          autoPlay
          style={{ visibility: noCamera ? 'hidden' : 'visible' }}
        />
      )}
      {noCamera && !frame && <div className="capture-feed capture-feed-fallback" aria-hidden />}

      <div className="capture-ribbon glass-ink">
        <span className="capture-title">
          <span className="kanji">体</span> Body composition
        </span>
        <button className="capture-x" aria-label="Close" onClick={onClose}>
          ✕
        </button>
      </div>

      {/* Framing guide while live */}
      {!frame && !noCamera && (
        <div className="body-guide" aria-hidden>
          <div className="body-guide-oval" />
          <div className="reticle-hint glass-ink">Stand back so your whole body fits</div>
        </div>
      )}

      {/* No-camera fallback */}
      {noCamera && !frame && (
        <div className="capture-center">
          <div className="glass capture-nocam">
            <div className="capture-nocam-icon">📷</div>
            <div className="card-title">Camera unavailable</div>
            <div className="small muted" style={{ margin: '6px 0 14px' }}>
              Pick a full-body photo from your gallery instead.
            </div>
            <button className="btn btn-primary" style={{ width: '100%' }} onClick={() => fileRef.current?.click()}>
              Choose from gallery
            </button>
            <button className="btn btn-ghost" style={{ width: '100%', marginTop: 8 }} onClick={onClose}>
              Pick from the diagram instead
            </button>
          </div>
        </div>
      )}

      {/* Analyzing shimmer */}
      {frame && analyzing && (
        <div className="capture-center">
          <div className={`glass-ink capture-shimmer ${reducedMotion ? '' : 'shimmering'}`}>
            <PetalLoader size={44} />
            <div>estimating body composition…</div>
          </div>
        </div>
      )}

      {/* Failure card: try again / enter manually */}
      {failure && !analyzing && !estimate && (
        <div className="capture-center">
          <div className="glass overlay-card">
            <div className="card-title">Couldn’t estimate</div>
            <div className="small muted" style={{ margin: '6px 0 14px' }}>{failure}</div>
            <div className="row between">
              <button className="btn btn-ghost" onClick={onClose}>
                Pick from diagram
              </button>
              <button className="btn btn-primary" onClick={() => { setFailure(null); }}>
                Try again
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Editable result */}
      {estimate && !analyzing && (
        <div className="capture-center">
          <div className="glass overlay-card body-result">
            <div className="card-title">Estimated body fat</div>
            <div className="body-result-pct tnum">
              <span className="big">{pct}</span>%
            </div>
            <div className="small muted" style={{ marginBottom: 8 }}>
              likely range {Math.round(range[0])}–{Math.round(range[1])}% · {Math.round(estimate.confidence * 100)}% confidence
            </div>
            <input
              type="range"
              min={6}
              max={45}
              step={1}
              value={pct}
              onChange={(e) => setPct(parseInt(e.target.value, 10))}
              aria-label="Adjust estimated body fat"
            />
            <div className="small muted" style={{ margin: '10px 0' }}>{estimate.notes}</div>
            <div className="small" style={{ color: 'var(--matcha)', marginBottom: 12 }}>
              ✓ Your photo was analyzed and discarded — nothing is stored.
            </div>
            <div className="row between">
              <button className="btn btn-ghost" onClick={onClose}>
                Cancel
              </button>
              <button className="btn btn-primary" onClick={() => onConfirm(pct)}>
                Save {pct}%
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Bottom bar: gallery + shutter (only while framing) */}
      {!frame && !noCamera && (
        <div className="capture-bar glass-ink">
          <button className="capture-gallery" aria-label="Pick from gallery" onClick={() => fileRef.current?.click()}>
            🖼
          </button>
          <button className="shutter" aria-label="Capture body photo" onClick={onShutter}>
            <span className="shutter-ring" />
            <span className="shutter-core" />
          </button>
          <span style={{ width: 60 }} />
        </div>
      )}

      <input ref={fileRef} className="hidden-input" type="file" accept="image/*" onChange={onGallery} />
    </div>
  );
}
