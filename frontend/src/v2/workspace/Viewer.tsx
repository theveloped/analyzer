import { useEffect, useRef } from 'react';
import { attach } from '../../viewer/controller';

/**
 * Mounts the shared three.js viewer controller (the same one the original app
 * uses) into a filling container. A ResizeObserver nudges the scene's
 * window-resize handler so the canvas tracks layout changes — e.g. when the
 * left sidebar collapses or the settings rail opens.
 */
export function Viewer() {
  const host = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!host.current) return;
    const detach = attach(host.current);
    const observer = new ResizeObserver(() => {
      window.dispatchEvent(new Event('resize'));
    });
    observer.observe(host.current);
    return () => {
      observer.disconnect();
      detach();
    };
  }, []);

  return <div ref={host} className="absolute inset-0 [&>canvas]:block [&>canvas]:size-full" />;
}
