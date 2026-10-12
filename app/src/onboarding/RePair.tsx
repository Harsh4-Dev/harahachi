// "This phone needs to be paired again" (API.md v4). Shown when the server
// answers 401 for an unknown/revoked device key — e.g. after the checkpoint
// moved to its new home, or the keeper revoked this phone. AppContext has
// already dropped the dead pairing and every volatile cache; this screen just
// explains and routes to Pairing.

import { useApp } from '../state/AppContext';

export function RePair() {
  const { goToPairing } = useApp();
  return (
    <div className="scroll-flow">
      <div className="scroll-sheet glass">
        <div className="scroll-top" aria-hidden />
        <div className="scroll-body">
          <div className="ob-step">
            <div className="ob-kanji">関所</div>
            <h1 className="display ob-title">This phone needs to be paired again</h1>
            <p className="muted">
              The checkpoint no longer recognises this phone — it may have moved to a new address, or this device was
              signed out by the keeper. Pair again with a fresh code from the admin page; your account and history are
              kept on the checkpoint.
            </p>
            <button className="btn btn-primary" style={{ width: '100%', marginTop: 12 }} onClick={goToPairing}>
              Pair this phone
            </button>
          </div>
        </div>
        <div className="scroll-bottom" aria-hidden />
      </div>
    </div>
  );
}
