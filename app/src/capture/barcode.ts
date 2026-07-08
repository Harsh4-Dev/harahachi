// Barcode detection abstraction. Prefers the native BarcodeDetector API (fast,
// zero-JS-decode, available on Chrome/Android WebView), and falls back to
// @zxing/browser when it's absent (iOS Safari, older WebViews). Exposes a tiny
// start/stop scanner that polls the given <video> and reports the first code.

import type { IScannerControls } from '@zxing/browser';

// Minimal shape of the native BarcodeDetector we rely on (not in lib.dom yet).
interface NativeBarcodeDetector {
  detect(source: CanvasImageSource): Promise<{ rawValue: string }[]>;
}
interface BarcodeDetectorCtor {
  new (opts?: { formats?: string[] }): NativeBarcodeDetector;
  getSupportedFormats?(): Promise<string[]>;
}

const BARCODE_FORMATS = [
  'ean_13',
  'ean_8',
  'upc_a',
  'upc_e',
  'code_128',
  'code_39',
  'itf',
];

export interface ScannerHandle {
  stop(): void;
}

function getNativeDetectorCtor(): BarcodeDetectorCtor | null {
  const w = window as unknown as { BarcodeDetector?: BarcodeDetectorCtor };
  return w.BarcodeDetector ?? null;
}

export function isNativeBarcodeSupported(): boolean {
  return getNativeDetectorCtor() != null;
}

// Starts scanning the live <video>. Calls onDetect exactly once with the first
// decoded code, then keeps running until stop() is called (the caller decides
// whether to reopen). Returns a handle to stop scanning + release resources.
export function startBarcodeScan(
  video: HTMLVideoElement,
  onDetect: (code: string) => void,
): ScannerHandle {
  const ctor = getNativeDetectorCtor();
  if (ctor) return startNative(ctor, video, onDetect);
  return startZxing(video, onDetect);
}

function startNative(
  ctor: BarcodeDetectorCtor,
  video: HTMLVideoElement,
  onDetect: (code: string) => void,
): ScannerHandle {
  const detector = new ctor({ formats: BARCODE_FORMATS });
  let stopped = false;
  let raf = 0;
  let fired = false;

  const tick = async () => {
    if (stopped) return;
    if (video.readyState >= 2 && video.videoWidth > 0) {
      try {
        const codes = await detector.detect(video);
        if (!stopped && !fired && codes.length > 0 && codes[0].rawValue) {
          fired = true;
          onDetect(codes[0].rawValue.trim());
          return;
        }
      } catch {
        /* transient decode error — keep polling */
      }
    }
    raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);

  return {
    stop() {
      stopped = true;
      cancelAnimationFrame(raf);
    },
  };
}

function startZxing(
  video: HTMLVideoElement,
  onDetect: (code: string) => void,
): ScannerHandle {
  let controls: IScannerControls | null = null;
  let fired = false;
  let stopped = false;

  // Lazy-load zxing only on the fallback path, so it stays out of the main
  // bundle for the common (native BarcodeDetector) case.
  void import('@zxing/browser')
    .then(({ BrowserMultiFormatReader }) => {
      if (stopped) return;
      const reader = new BrowserMultiFormatReader();
      // Decode directly from the already-running <video> element's stream.
      return reader
        .decodeFromVideoElement(video, (result) => {
          if (stopped || fired || !result) return;
          fired = true;
          onDetect(result.getText().trim());
        })
        .then((c) => {
          controls = c;
          if (stopped) c.stop();
        });
    })
    .catch(() => {
      /* zxing failed to load/attach — nothing to detect */
    });

  return {
    stop() {
      stopped = true;
      controls?.stop();
    },
  };
}
