// Full-screen camera capture (DESIGN.md §6.5). Live rear-camera feed with a
// glass top ribbon + glass bottom bar (shutter, gallery fallback, Dish/Barcode
// toggle). Dish mode freezes the frame, POSTs to /meals/analyze, and floats
// glass overlay cards over the photo. Barcode mode overlays a glass reticle and
// detects codes (native BarcodeDetector → @zxing/browser fallback), then floats
// a product card. Both confirm with the vermillion hanko stamp.
//
// The file input remains as the gallery button AND the no-permission fallback.

import { useCallback, useEffect, useRef, useState } from 'react';
import { useCamera, type Frame } from './useCamera';
import { startBarcodeScan, type ScannerHandle } from './barcode';
import { DishOverlay } from './DishOverlay';
import { BarcodeCard } from './BarcodeCard';
import { PetalLoader } from '../components/PetalLoader';
import { useApp } from '../state/AppContext';
import { ApiError } from '../api/http';
import type { AnalyzeResult, BarcodeResult, MealInput } from '../api/types';

type CaptureMode = 'dish' | 'barcode';

interface Props {
  onClose: () => void;
  onLogged: () => void;
  reducedMotion: boolean;
}

export function Capture({ onClose, onLogged, reducedMotion }: Props) {
  const { client, logMeal, showError, toast } = useApp();
  const [mode, setMode] = useState<CaptureMode>('dish');
  const { videoRef, status, captureFrame } = useCamera(true);
  const fileRef = useRef<HTMLInputElement | null>(null);

  // Dish flow state
  const [frame, setFrame] = useState<Frame | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [result, setResult] = useState<AnalyzeResult | null>(null);

  // Barcode flow state
  const [product, setProduct] = useState<BarcodeResult | null>(null);
  const [lookingUp, setLookingUp] = useState(false);
  const scannerRef = useRef<ScannerHandle | null>(null);
  const lookingUpRef = useRef(false);

  const noCamera = status === 'denied' || status === 'unsupported';

  const revokeFrame = useCallback((f: Frame | null) => {
    if (f) URL.revokeObjectURL(f.url);
  }, []);

  // --- Dish: run analysis on a frozen frame (from shutter or gallery) ---
  const runAnalyze = useCallback(
    async (f: Frame) => {
      setFrame((prev) => {
        revokeFrame(prev);
        return f;
      });
      setResult(null);
      setAnalyzing(true);
      try {
        const r = await client.analyzeMeal(f.blob);
        setResult(r);
      } catch (e) {
        const net = e instanceof ApiError && e.status === 0;
        revokeFrame(f);
        setFrame(null);
        // Shared error surface: retry the analysis, or fall back to manual entry.
        showError({
          message: net
            ? 'Sekisho is unreachable — check the server or your connection.'
            : 'Could not read the dish.',
          retry: () => void runAnalyze({ ...f, url: URL.createObjectURL(f.blob) }),
          secondaryLabel: 'Enter manually',
          onSecondary: onClose, // hands back to Chaya where manual entry lives
        });
      } finally {
        setAnalyzing(false);
      }
    },
    [client, showError, revokeFrame, onClose],
  );

  const onShutter = useCallback(async () => {
    if (mode !== 'dish' || analyzing || result) return;
    const f = await captureFrame();
    if (!f) {
      toast('Camera not ready — use the gallery button.', 'warn');
      return;
    }
    void runAnalyze(f);
  }, [mode, analyzing, result, captureFrame, runAnalyze, toast]);

  const onGallery = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    const url = URL.createObjectURL(file);
    // Load natural dimensions so the overlay can map item points through the
    // object-fit:cover crop (otherwise chips drift on non-viewport aspect ratios).
    const img = new Image();
    img.onload = () => {
      void runAnalyze({ blob: file, url, width: img.naturalWidth, height: img.naturalHeight });
    };
    img.onerror = () => {
      void runAnalyze({ blob: file, url, width: 0, height: 0 });
    };
    img.src = url;
  }, [runAnalyze]);

  const resetDish = useCallback(() => {
    setResult(null);
    setAnalyzing(false);
    setFrame((prev) => {
      revokeFrame(prev);
      return null;
    });
  }, [revokeFrame]);

  // --- Barcode: scan the live feed while in barcode mode with no product shown ---
  const onDetected = useCallback(
    async (code: string) => {
      if (lookingUpRef.current) return;
      lookingUpRef.current = true;
      setLookingUp(true);
      scannerRef.current?.stop();
      scannerRef.current = null;
      if ('vibrate' in navigator) navigator.vibrate?.(20);
      try {
        const r = await client.lookupBarcode(code);
        setProduct(r);
      } catch (e) {
        const net = e instanceof ApiError && e.status === 0;
        lookingUpRef.current = false;
        setLookingUp(false);
        // Barcode lookup failure → retry (re-scan) or manual entry.
        showError({
          message: net
            ? 'Sekisho is unreachable — check the server or your connection.'
            : 'Barcode lookup failed.',
          retry: () => {
            lookingUpRef.current = false;
            setLookingUp(false);
          },
          secondaryLabel: 'Enter manually',
          onSecondary: onClose,
        });
      }
    },
    [client, showError, onClose],
  );

  useEffect(() => {
    if (mode !== 'barcode' || product || status !== 'live') return;
    const v = videoRef.current;
    if (!v) return;
    lookingUpRef.current = false;
    const handle = startBarcodeScan(v, (code) => void onDetected(code));
    scannerRef.current = handle;
    return () => {
      handle.stop();
      scannerRef.current = null;
    };
  }, [mode, product, status, videoRef, onDetected]);

  const resetBarcode = useCallback(() => {
    setProduct(null);
    setLookingUp(false);
    lookingUpRef.current = false;
  }, []);

  // Switching modes clears any in-flight results.
  const switchMode = useCallback(
    (m: CaptureMode) => {
      if (m === mode) return;
      resetDish();
      resetBarcode();
      setMode(m);
    },
    [mode, resetDish, resetBarcode],
  );

  // --- Logging (shared hanko stamp target) ---
  const doLog = useCallback(
    async (meal: MealInput) => {
      await logMeal(meal);
      toast('Stamped — the traveler moves on.', 'ok');
      onLogged();
    },
    [logMeal, toast, onLogged],
  );

  return (
    <div className="capture-full" role="dialog" aria-label="Capture">
      {/* Live feed / frozen frame background */}
      {frame ? (
        <img className="capture-feed" src={frame.url} alt="Captured dish" />
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
      {noCamera && <div className="capture-feed capture-feed-fallback" aria-hidden />}

      {/* Top glass ribbon */}
      <div className="capture-ribbon glass-ink">
        <span className="capture-title">
          <span className="kanji">茶屋</span> Chaya · Capture
        </span>
        <button className="capture-x" aria-label="Close capture" onClick={onClose}>
          ✕
        </button>
      </div>

      {/* Barcode reticle (only while actively scanning) */}
      {mode === 'barcode' && !product && !lookingUp && !noCamera && (
        <div className="reticle" aria-hidden>
          <span className="tick tl" />
          <span className="tick tr" />
          <span className="tick bl" />
          <span className="tick br" />
          {!reducedMotion && <span className="scanline" />}
          <div className="reticle-hint glass-ink">Point at a barcode</div>
        </div>
      )}

      {/* Barcode lookup in flight — petal loader over the frozen feed */}
      {mode === 'barcode' && lookingUp && !product && (
        <div className="capture-center">
          <div className={`glass-ink capture-shimmer ${reducedMotion ? '' : 'shimmering'}`}>
            <PetalLoader size={44} />
            <div>looking up…</div>
          </div>
        </div>
      )}

      {/* No-camera graceful glass card */}
      {noCamera && !frame && !product && (
        <div className="capture-center">
          <div className="glass capture-nocam">
            <div className="capture-nocam-icon">📷</div>
            <div className="card-title">
              {status === 'denied' ? 'Camera permission needed' : 'Camera unavailable here'}
            </div>
            <div className="small muted" style={{ margin: '6px 0 14px' }}>
              {status === 'denied'
                ? 'Allow camera access, or pick a photo from your gallery instead.'
                : 'This device or browser has no usable camera. Pick a photo from your gallery instead.'}
            </div>
            <button className="btn btn-primary" style={{ width: '100%' }} onClick={() => fileRef.current?.click()}>
              Choose from gallery
            </button>
          </div>
        </div>
      )}

      {/* Dish analysis: shimmer then overlay cards */}
      {mode === 'dish' && frame && analyzing && (
        <div className="capture-center">
          <div className={`glass-ink capture-shimmer ${reducedMotion ? '' : 'shimmering'}`}>
            <PetalLoader size={44} />
            <div>reading the dish…</div>
          </div>
        </div>
      )}
      {mode === 'dish' && frame && result && (
        <DishOverlay
          result={result}
          reducedMotion={reducedMotion}
          imgW={frame.width}
          imgH={frame.height}
          onRetake={resetDish}
          onLog={doLog}
        />
      )}

      {/* Barcode product / not-found card */}
      {mode === 'barcode' && product && (
        <BarcodeCard result={product} onRescan={resetBarcode} onLog={doLog} onManual={onClose} />
      )}

      {/* Bottom glass bar: gallery · shutter · mode toggle */}
      {!frame && !product && (
        <div className="capture-bar glass-ink">
          <button
            className="capture-gallery"
            aria-label="Pick from gallery"
            onClick={() => fileRef.current?.click()}
          >
            🖼
          </button>

          <button
            className={`shutter ${mode === 'barcode' ? 'shutter-hidden' : ''}`}
            aria-label="Take photo"
            onClick={onShutter}
            disabled={mode === 'barcode' || noCamera}
          >
            <span className="shutter-ring" />
            <span className="shutter-core" />
          </button>

          <div className="mode-toggle" role="tablist" aria-label="Capture mode">
            <button
              role="tab"
              aria-selected={mode === 'dish'}
              className={`mode-pill ${mode === 'dish' ? 'active' : ''}`}
              onClick={() => switchMode('dish')}
            >
              料理<span className="mode-en">Dish</span>
            </button>
            <button
              role="tab"
              aria-selected={mode === 'barcode'}
              className={`mode-pill ${mode === 'barcode' ? 'active' : ''}`}
              onClick={() => switchMode('barcode')}
            >
              バーコード<span className="mode-en">Barcode</span>
            </button>
          </div>
        </div>
      )}

      <input
        ref={fileRef}
        className="hidden-input"
        type="file"
        accept="image/*"
        onChange={onGallery}
      />
    </div>
  );
}
