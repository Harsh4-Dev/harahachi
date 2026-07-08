// Rear-camera getUserMedia hook for the full-screen capture view. Returns a
// ref to attach to a <video>, a status, and a captureFrame() that freezes the
// current frame to a JPEG Blob + object-URL. Cleans the stream up on unmount.

import { useCallback, useEffect, useRef, useState } from 'react';

export type CameraStatus = 'idle' | 'starting' | 'live' | 'denied' | 'unsupported';

export interface Frame {
  blob: Blob;
  url: string;
  width: number;
  height: number;
}

export function useCamera(active: boolean) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [status, setStatus] = useState<CameraStatus>('idle');

  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }, []);

  useEffect(() => {
    if (!active) {
      stop();
      setStatus('idle');
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setStatus('unsupported');
      return;
    }
    let cancelled = false;
    setStatus('starting');
    navigator.mediaDevices
      .getUserMedia({
        video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 } },
        audio: false,
      })
      .then((stream) => {
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;
        const v = videoRef.current;
        if (v) {
          v.srcObject = stream;
          void v.play().catch(() => {});
        }
        setStatus('live');
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        const name = (e as { name?: string })?.name;
        setStatus(name === 'NotAllowedError' || name === 'SecurityError' ? 'denied' : 'unsupported');
      });
    return () => {
      cancelled = true;
      stop();
    };
  }, [active, stop]);

  // Freeze the current video frame into a JPEG blob + object URL.
  const captureFrame = useCallback(async (): Promise<Frame | null> => {
    const v = videoRef.current;
    if (!v || !v.videoWidth) return null;
    const canvas = document.createElement('canvas');
    canvas.width = v.videoWidth;
    canvas.height = v.videoHeight;
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    ctx.drawImage(v, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise<Blob | null>((res) =>
      canvas.toBlob((b) => res(b), 'image/jpeg', 0.9),
    );
    if (!blob) return null;
    return { blob, url: URL.createObjectURL(blob), width: canvas.width, height: canvas.height };
  }, []);

  return { videoRef, status, captureFrame, stop };
}
