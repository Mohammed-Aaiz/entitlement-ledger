import { useNavigate } from 'react-router-dom';
import { useState } from 'react';
import { ApiError } from '../api/client';

export interface ErrorDisplayInfo {
  title: string;
  explanation: string;
  code?: string;
  requestId?: string;
  retryable?: boolean;
  status?: number;
}

/** Map an unknown thrown value to a safe, user-friendly error display. */
export function toErrorDisplay(err: unknown): ErrorDisplayInfo {
  if (err instanceof ApiError) {
    switch (err.status) {
      case 401:
        return {
          title: 'Session expired',
          explanation: 'Your session has ended. Please sign in again to continue.',
          code: err.code,
          requestId: err.requestId,
          retryable: false,
          status: err.status,
        };
      case 403:
        return {
          title: 'Access denied',
          explanation: 'You do not have permission to view this information.',
          code: err.code,
          requestId: err.requestId,
          retryable: false,
          status: err.status,
        };
      case 404:
        return {
          title: 'Not found',
          explanation: 'The requested record could not be found. It may have been removed.',
          code: err.code,
          requestId: err.requestId,
          retryable: false,
          status: err.status,
        };
      case 429:
        return {
          title: 'Too many requests',
          explanation: 'The server is busy. Wait a moment and try again.',
          code: err.code,
          requestId: err.requestId,
          retryable: true,
          status: err.status,
        };
      case 0:
        return {
          title: 'Connection problem',
          explanation: 'The application could not reach the server. Check your connection and retry.',
          code: err.code,
          requestId: err.requestId,
          retryable: true,
          status: err.status,
        };
      default:
        return {
          title: err.status >= 500 ? 'Service unavailable' : 'Something went wrong',
          explanation: err.message || 'An unexpected problem occurred. Please try again.',
          code: err.code,
          requestId: err.requestId,
          retryable: err.retryable,
          status: err.status,
        };
    }
  }
  if (err instanceof Error) {
    // Never surface raw exception text — only a generic explanation.
    return {
      title: 'Something went wrong',
      explanation: 'The application hit an unexpected problem. Your data is safe — please retry.',
      retryable: true,
    };
  }
  return {
    title: 'Something went wrong',
    explanation: 'An unexpected problem occurred. Please retry.',
    retryable: true,
  };
}

interface ProductErrorStateProps {
  error: unknown;
  onRetry?: () => void;
  compact?: boolean;
}

/**
 * Product-level error landing state.
 *
 * Renders a friendly title + explanation in the existing visual language.
 * Never renders stack traces, raw JSON, Python exceptions or provider error
 * bodies. Shows the request id when the backend envelope provided one.
 */
export default function ProductErrorState({ error, onRetry, compact = false }: ProductErrorStateProps) {
  const navigate = useNavigate();
  const [retrying, setRetrying] = useState(false);
  const info = toErrorDisplay(error);

  const handleRetry = () => {
    if (!onRetry) return;
    setRetrying(true);
    // Let the caller re-run its request; the retrying state clears on next render.
    Promise.resolve(onRetry()).finally(() => setRetrying(false));
  };

  return (
    <div className={`surface flex ${compact ? 'flex-col items-start gap-3 p-5' : 'flex-col items-center justify-center p-10 text-center'}`}>
      <div className="mb-2 inline-flex h-10 w-10 items-center justify-center rounded-full border border-amber-400/25 bg-amber-400/10 text-amber-600">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M8 5.5v3.5M8 11.5h.01M8 2.5 1.5 13h13L8 2.5Z" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </div>

      <div className={compact ? '' : 'max-w-md'}>
        <h2 className="text-[15px] font-semibold text-stone-900">{info.title}</h2>
        <p className="mt-1.5 text-[13px] leading-relaxed text-stone-500">{info.explanation}</p>

        {info.requestId && (
          <p className="mt-2 font-mono text-[10px] text-stone-400">
            Request ID: {info.requestId}
          </p>
        )}
        {info.code && (
          <p className="mt-1 font-mono text-[10px] uppercase tracking-wide text-stone-400">
            {info.code}
          </p>
        )}
      </div>

      <div className={`flex flex-wrap items-center gap-2 ${compact ? 'mt-1' : 'mt-5 justify-center'}`}>
        {onRetry && info.retryable !== false && (
          <button
            type="button"
            onClick={handleRetry}
            disabled={retrying}
            className="rounded-lg bg-stone-900 px-4 py-1.5 text-[12px] font-semibold text-white transition-colors hover:bg-stone-700 disabled:opacity-60"
          >
            {retrying ? 'Retrying…' : 'Retry'}
          </button>
        )}
        <button
          type="button"
          onClick={() => navigate('/finance-control-room')}
          className="rounded-lg border border-stone-200 px-4 py-1.5 text-[12px] font-semibold text-stone-600 transition-colors hover:border-stone-300 hover:text-stone-900"
        >
          Go to Control Room
        </button>
      </div>
    </div>
  );
}
