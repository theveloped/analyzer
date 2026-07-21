import { useEffect, useRef } from 'react';
import { attach, setViewerTheme } from '../../viewer/controller';
import { useV2 } from '../store';

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
    // a remount builds a fresh Scene3D with the default (dark) background —
    // re-apply the current theme (e.g. returning from a published report)
    setViewerTheme(useV2.getState().theme);
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
