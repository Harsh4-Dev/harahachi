// Account screen (accounts v2, DESIGN.md 6.6): tegata-scroll styled. Two modes:
//  • create — username + 4-8 digit PIN on a glass numpad, sealed by the hanko
//    stamp → POST /auth/register (profile carried from onboarding draft). Shown
//    right after pairing for a first-time traveler.
//  • login — username + PIN pad + "new traveler" link → create. Shown when the
//    device is paired but logged out (or on "switch account").
//
// Failure UX: register/login failures surface inline with a retry; network/
// timeout uses the shared error surface message.

import { useState } from 'react';
import { useApp } from '../state/AppContext';
import { ApiError } from '../api/http';
import { getDraftProfile, clearDraftProfile } from '../api/storage';
import type { Profile } from '../api/types';

type Mode = 'create' | 'login';

const NEUTRAL_PROFILE: Profile = {
  name: '',
  age: 0,
  sex: 'x',
  height_cm: 0,
  weight_kg: 0,
  activity: 'moderate',
  goal: 'maintain',
};

export function Account() {
  const { client, onRegistered, onLoggedIn, goToPairing } = useApp();
  const draft = getDraftProfile();
  // A pending onboarding draft means this is a first-time traveler → create.
  const [mode, setMode] = useState<Mode>(draft ? 'create' : 'login');
  const [username, setUsername] = useState('');
  const [pin, setPin] = useState('');
  const [busy, setBusy] = useState(false);
  const [stamped, setStamped] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const pinOk = pin.length >= 4 && pin.length <= 8;
  const userOk = username.trim().length >= 2;
  const canSubmit = pinOk && userOk && !busy;

  const pressDigit = (d: string) => {
    setErr(null);
    setPin((p) => (p.length < 8 ? p + d : p));
  };
  const backspace = () => setPin((p) => p.slice(0, -1));

  const submit = async () => {
    if (!canSubmit) return;
    setBusy(true);
    setErr(null);
    try {
      if (mode === 'create') {
        const profile = draft ?? NEUTRAL_PROFILE;
        const res = await client.register({ username: username.trim(), pin, profile });
        setStamped(true);
        if ('vibrate' in navigator) navigator.vibrate?.(40);
        clearDraftProfile();
        window.setTimeout(() => onRegistered(res.user), 650);
      } else {
        const res = await client.login(username.trim(), pin);
        clearDraftProfile();
        onLoggedIn(res.user);
      }
    } catch (e) {
      setBusy(false);
      setPin('');
      if (e instanceof ApiError && e.status === 0) {
        setErr('Sekisho is unreachable — check the server or your connection.');
      } else if (e instanceof ApiError && (e.code === 'locked' || e.status === 429)) {
        setErr('Too many attempts — wait a minute and try again.');
      } else if (e instanceof ApiError && (e.code === 'bad_credentials' || e.status === 401)) {
        setErr('Wrong username or PIN.');
      } else if (e instanceof ApiError && (e.code === 'username_taken' || e.status === 409)) {
        setErr('That username is taken — pick another.');
      } else {
        setErr(mode === 'create' ? 'Could not create the account.' : 'Could not sign in.');
      }
    }
  };

  const initial = (username.trim() || 'T').slice(0, 1).toUpperCase();

  return (
    <div className="scroll-flow">
      <div className="scroll-sheet glass">
        <div className="scroll-top" aria-hidden />
        <div className="scroll-body">
          <div className="ob-step">
            <div className="ob-kanji">{mode === 'create' ? '登録' : '合札'}</div>
            <h1 className="display ob-title">
              {mode === 'create' ? 'Create your account' : 'Welcome back, traveler'}
            </h1>
            <p className="muted">
              {mode === 'create'
                ? 'Choose a name and a 4–8 digit PIN. The hanko stamp seals your permit.'
                : 'Sign in with your username and PIN to pick up your journey.'}
            </p>

            <div className="field" style={{ marginTop: 14 }}>
              <label>Username</label>
              <input
                className="input"
                value={username}
                onChange={(e) => {
                  setErr(null);
                  setUsername(e.target.value);
                }}
                placeholder="traveler"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
              />
            </div>

            <div className="field">
              <label>PIN {pin.length > 0 && <span className="muted">({pin.length} digit{pin.length > 1 ? 's' : ''})</span>}</label>
              <div className="pin-dots" aria-label={`PIN, ${pin.length} of up to 8 digits`}>
                {Array.from({ length: Math.max(4, pin.length) }).map((_, i) => (
                  <span key={i} className={`pin-dot ${i < pin.length ? 'filled' : ''}`} />
                ))}
              </div>
            </div>

            <div className="numpad">
              {['1', '2', '3', '4', '5', '6', '7', '8', '9'].map((d) => (
                <button key={d} type="button" className="numkey glass" onClick={() => pressDigit(d)}>
                  {d}
                </button>
              ))}
              <span className="numkey-spacer" />
              <button type="button" className="numkey glass" onClick={() => pressDigit('0')}>
                0
              </button>
              <button type="button" className="numkey glass numkey-back" onClick={backspace} aria-label="Delete">
                ⌫
              </button>
            </div>

            {err && <div className="account-err small">{err}</div>}

            {mode === 'create' ? (
              <div className="center" style={{ marginTop: 8 }}>
                <button
                  className={`big-hanko ${stamped ? 'stamped' : ''}`}
                  onClick={submit}
                  disabled={!canSubmit}
                  aria-label="Stamp to create account"
                  style={{ opacity: canSubmit ? 1 : 0.45 }}
                >
                  <span className="big-hanko-initial">{initial}</span>
                </button>
                <p className="small muted">{busy ? 'Sealing…' : canSubmit ? 'Tap the seal to create' : 'Enter a name and PIN first'}</p>
              </div>
            ) : (
              <button className="btn btn-primary" style={{ width: '100%', marginTop: 10 }} onClick={submit} disabled={!canSubmit}>
                {busy ? 'Signing in…' : 'Sign in'}
              </button>
            )}
          </div>
        </div>

        <div className="scroll-actions center" style={{ justifyContent: 'center', flexDirection: 'column', gap: 6 }}>
          {mode === 'login' ? (
            <button
              className="link-btn"
              onClick={() => {
                setErr(null);
                setPin('');
                setMode('create');
              }}
            >
              new traveler? create an account →
            </button>
          ) : (
            !draft && (
              <button
                className="link-btn"
                onClick={() => {
                  setErr(null);
                  setPin('');
                  setMode('login');
                }}
              >
                ← already have an account? sign in
              </button>
            )
          )}
          <button className="link-btn" onClick={goToPairing} disabled={busy}>
            re-pair a different checkpoint
          </button>
        </div>
        <div className="scroll-bottom" aria-hidden />
      </div>
    </div>
  );
}
