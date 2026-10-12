// Pairing at the Sekisho checkpoint (関所). Scan the admin QR with the camera
// (qr-scanner) or fall back to pasting the token. A "try demo" button skips
// pairing entirely with stubbed data.
//
// v4 (Vercel): the PWA is served from the same origin as the API, so its manual
// form needs only the code (a different checkpoint address is tucked behind a
// disclosure for development). The APK (isNativeShell) has no usable origin and
// MUST be given the stable https:// deployment address — from the QR or typed.
// Every address is normalised by lib/serverUrl (https only, except localhost).
// This screen and the keeper screen are the only places addresses appear.

import { useEffect, useRef, useState } from 'react';
import QrScanner from 'qr-scanner';
import { claimPairing, ApiError } from '../api/http';
import { isNativeShell } from '../api/storage';
import { normalizeServerUrl } from '../lib/serverUrl';
import { useApp } from '../state/AppContext';

interface QrPayload {
  v?: number;
  url?: string;
  token?: string;
}

function parseQr(data: string): { token: string; url?: string } | null {
  try {
    const obj = JSON.parse(data) as QrPayload;
    if (obj && typeof obj.token === 'string') {
      return { token: obj.token, url: typeof obj.url === 'string' ? obj.url : undefined };
    }
  } catch {
    // Not JSON — maybe the raw token was encoded.
    if (data.trim()) return { token: data.trim() };
  }
  return null;
}

export function Pairing() {
  const { startDemo, finishPairing, showError, toast } = useApp();
  const [scanning, setScanning] = useState(false);
  const [manual, setManual] = useState(false);
  const [token, setToken] = useState('');
  const [serverUrl, setServerUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [camError, setCamError] = useState<string | null>(null);
  const [urlError, setUrlError] = useState<string | null>(null);
  const native = isNativeShell();
  // PWA: the address field is optional and hidden until asked for.
  const [showAddress, setShowAddress] = useState(native);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const scannerRef = useRef<QrScanner | null>(null);

  // Start/stop the QR scanner when entering/leaving scan mode.
  useEffect(() => {
    if (!scanning || !videoRef.current) return;
    let disposed = false;
    const scanner = new QrScanner(
      videoRef.current,
      (res) => {
        const parsed = parseQr(res.data);
        if (parsed) {
          void doClaim(parsed.token, parsed.url);
          scanner.stop();
          setScanning(false);
        }
      },
      { returnDetailedScanResult: true, highlightScanRegion: true, preferredCamera: 'environment' },
    );
    scannerRef.current = scanner;
    scanner.start().catch((e: unknown) => {
      if (disposed) return;
      setCamError((e as Error)?.message || 'Camera unavailable');
      setScanning(false);
      setManual(true);
    });
    return () => {
      disposed = true;
      scanner.stop();
      scanner.destroy();
      scannerRef.current = null;
    };
    // doClaim is stable enough for this flow; deps kept minimal intentionally.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scanning]);

  const doClaim = async (tok: string, url?: string) => {
    if (busy) return;
    // Priority: scanned/typed URL → manual address field → same-origin.
    // In the native shell (APK) same-origin is unusable, so a URL is required.
    const typed = url || serverUrl.trim();
    let base: string;
    if (typed) {
      const n = normalizeServerUrl(typed);
      if (!n.ok) {
        setUrlError(n.error);
        setShowAddress(true);
        setScanning(false);
        setManual(true);
        return;
      }
      base = n.url;
    } else if (native) {
      setUrlError('Enter the checkpoint address — it’s shown with the pairing QR.');
      setShowAddress(true);
      setScanning(false);
      setManual(true);
      return;
    } else {
      base = window.location.origin;
    }
    setUrlError(null);
    setBusy(true);
    try {
      const { device_id, device_key } = await claimPairing(base, tok.trim(), deviceName());
      finishPairing({ baseUrl: base, deviceKey: device_key, deviceId: device_id });
      toast('Paired — welcome to the road.', 'ok');
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? e.status === 0
            ? typed
              ? 'Sekisho is unreachable — check the checkpoint address or your connection.'
              : 'Sekisho is unreachable — check your connection.'
            : e.message || 'Pairing failed.'
          : 'Pairing failed.';
      setBusy(false);
      // Shared error surface: retry the claim, or fall back to typing the code.
      showError({
        message: msg,
        retry: () => void doClaim(tok, url),
        secondaryLabel: 'Enter code manually',
        onSecondary: () => {
          setScanning(false);
          setManual(true);
        },
      });
    }
  };

  const submitManual = () => {
    if (!token.trim()) return;
    void doClaim(token, serverUrl.trim() || undefined);
  };

  return (
    <div className="scroll-flow">
      <div className="scroll-sheet glass">
        <div className="scroll-top" aria-hidden />
        <div className="scroll-body">
          <div className="ob-step">
            <div className="ob-kanji">関所</div>
            <h1 className="display ob-title">Pair with the checkpoint</h1>
            <p className="muted">
              On any computer, open the checkpoint’s admin page and choose <em>Pair new device</em>. Point your camera
              at the QR, or type the code{native ? ' and the checkpoint address shown with it' : ''}.
            </p>

            {scanning ? (
              <div className="qr-frame">
                <video ref={videoRef} className="qr-video" muted playsInline />
                <button className="btn btn-ghost" style={{ marginTop: 12, width: '100%' }} onClick={() => setScanning(false)}>
                  Stop camera
                </button>
              </div>
            ) : manual ? (
              <div style={{ marginTop: 8 }}>
                {camError && <div className="small muted" style={{ marginBottom: 8 }}>Camera: {camError}</div>}
                <div className="field">
                  <label>Pairing code</label>
                  <input
                    className="input"
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    placeholder="paste the token"
                    autoFocus
                  />
                </div>
                {showAddress ? (
                  <div className="field">
                    <label>
                      Checkpoint address{' '}
                      {!native && <span className="muted">(optional — leave blank for this site)</span>}
                    </label>
                    <input
                      className="input"
                      value={serverUrl}
                      onChange={(e) => {
                        setServerUrl(e.target.value);
                        setUrlError(null);
                      }}
                      onBlur={() => {
                        // Show the cleaned-up form so the user sees what will be used.
                        if (!serverUrl.trim()) return;
                        const n = normalizeServerUrl(serverUrl);
                        if (n.ok) setServerUrl(n.url);
                        else setUrlError(n.error);
                      }}
                      placeholder="https://your-checkpoint.vercel.app"
                      inputMode="url"
                      autoCapitalize="off"
                      autoCorrect="off"
                      spellCheck={false}
                      aria-invalid={!!urlError}
                    />
                    {urlError && (
                      <div className="small" role="alert" style={{ color: 'var(--shu)', marginTop: 6 }}>
                        {urlError}
                      </div>
                    )}
                  </div>
                ) : (
                  <button className="link-btn" style={{ marginBottom: 10 }} onClick={() => setShowAddress(true)}>
                    Pairing with a different checkpoint?
                  </button>
                )}
                <button
                  className="btn btn-primary"
                  style={{ width: '100%' }}
                  onClick={submitManual}
                  disabled={busy || !token.trim() || (native && !serverUrl.trim())}
                >
                  {busy ? 'Pairing…' : 'Pair device'}
                </button>
                <button className="btn btn-ghost" style={{ width: '100%', marginTop: 8 }} onClick={() => setManual(false)}>
                  Use camera instead
                </button>
              </div>
            ) : (
              <div style={{ marginTop: 8 }}>
                <button className="btn btn-primary" style={{ width: '100%' }} onClick={() => setScanning(true)} disabled={busy}>
                  Scan QR with camera
                </button>
                <button className="btn btn-ghost" style={{ width: '100%', marginTop: 8 }} onClick={() => setManual(true)}>
                  Enter code manually
                </button>
              </div>
            )}
          </div>
        </div>

        <div className="scroll-actions center" style={{ justifyContent: 'center' }}>
          <button className="link-btn" onClick={startDemo} disabled={busy}>
            or try the demo with sample data →
          </button>
        </div>
        <div className="scroll-bottom" aria-hidden />
      </div>
    </div>
  );
}

function deviceName(): string {
  const ua = navigator.userAgent;
  if (/android/i.test(ua)) return 'Android phone';
  if (/iphone|ipad/i.test(ua)) return 'iPhone';
  return 'Phone';
}
