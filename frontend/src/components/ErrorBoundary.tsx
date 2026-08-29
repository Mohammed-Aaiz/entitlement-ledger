import { Component, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('EntitlementLedger error boundary caught:', error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex min-h-screen items-center justify-center bg-[#0B0A0F] p-8">
          <div className="max-w-md text-center">
            <div className="mb-4 inline-flex h-12 w-12 items-center justify-center rounded-full border border-[#F87171]/25 bg-[#F87171]/10 text-[#F87171]">
              ✗
            </div>
            <h1 className="text-lg font-semibold text-[#F5F7FA]">Unexpected error</h1>
            <p className="mt-2 text-sm leading-relaxed text-[#8B95A5]">
              The application encountered an error it could not recover from. Try reloading the page.
            </p>
            {this.state.error && (
              <pre className="mt-4 overflow-auto rounded-lg border border-white/[0.08] bg-[#120F17] p-3 text-left text-[11px] leading-relaxed text-[#8B95A5]">
                {this.state.error.message}
              </pre>
            )}
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="mt-6 rounded-lg bg-[#D9A441] px-5 py-2 text-sm font-semibold text-[#0B0A0F] hover:bg-[#E0B24E] transition-colors"
            >
              Reload application
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
