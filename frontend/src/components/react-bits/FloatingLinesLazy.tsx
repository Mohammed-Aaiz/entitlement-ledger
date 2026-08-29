import { Suspense, lazy } from 'react';

/**
 * Lazy-loaded FloatingLines so Three.js is only fetched when the
 * Dashboard hero actually renders. Falls back to an empty layer.
 */
const FloatingLinesInner = lazy(() => import('./FloatingLines'));

export default function FloatingLinesLazy(props: Record<string, unknown>) {
  return (
    <Suspense fallback={<div className="floating-lines-container" aria-hidden="true" />}>
      <FloatingLinesInner {...props} />
    </Suspense>
  );
}
