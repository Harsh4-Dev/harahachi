// Global error boundary (user requirement: never a white screen). Catches
// render-time crashes and shows a glass "something broke — reload" card instead
// of a blank page.

import { Component, type ReactNode } from 'react';

interface State {
  crashed: boolean;
  message: string;
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { crashed: false, message: '' };

  static getDerivedStateFromError(err: unknown): State {
    return { crashed: true, message: (err as Error)?.message || 'Unexpected error' };
  }

  componentDidCatch(err: unknown) {
    // eslint-disable-next-line no-console
    console.error('App crashed:', err);
  }

  render() {
    if (!this.state.crashed) return this.props.children;
    return (
      <div className="crash-screen">
        <div className="glass crash-card">
          <div className="ob-kanji" style={{ color: 'var(--shu)' }}>破</div>
          <h1 className="display" style={{ fontSize: 22, marginBottom: 6 }}>Something broke</h1>
          <p className="muted small" style={{ marginBottom: 16 }}>
            The road hit a snag. Reloading usually fixes it — your data is safe on the checkpoint.
          </p>
          <button className="btn btn-primary" style={{ width: '100%' }} onClick={() => window.location.reload()}>
            Reload the app
          </button>
        </div>
      </div>
    );
  }
}
