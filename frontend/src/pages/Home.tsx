import { useState, useEffect } from 'react';


function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const onChange = () => setReduced(mq.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);
  return reduced;
}

export default function Home() {
  const reducedMotion = useReducedMotion();

  return (
    <div className="relative min-h-[80vh] flex flex-col items-center justify-center px-6">
      <div className="text-center max-w-2xl mx-auto pointer-events-none">
        <h1
          className="text-4xl font-bold leading-tight tracking-tight text-stone-900 sm:text-5xl lg:text-7xl crossfade-enter drop-shadow-sm"
          style={{ animationDelay: reducedMotion ? '0ms' : '100ms' }}
        >
          ENTITLEMENTLEDGER
        </h1>

        <p
          className="mt-6 text-base sm:text-xl font-medium text-stone-700 crossfade-enter"
          style={{ animationDelay: reducedMotion ? '0ms' : '300ms' }}
        >
          Financial decision provenance for marketplace finance teams.
        </p>
      </div>
    </div>
  );
}
