import { useEffect, useRef } from 'react';
import {
  attach, captureViewer, setViewerTheme, setViewportState,
} from '../../viewer/controller';
import { initMeasureTool, syncMeasureAnnotations } from '../measure/tool';
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
    // same for the viewport state: push the store slice into the fresh scene,
    // then keep pushing on every change (the controller never imports the v2
    // store — the v1 app must stay decoupled from it)
    setViewportState(useV2.getState().viewport);
    const unsubscribe = useV2.subscribe((s, prev) => {
      if (s.viewport !== prev.viewport) setViewportState(s.viewport);
    });
    // measure tool: session → pick interceptor + annotation layer (and the
    // fresh scene needs the current annotations re-pushed, like the theme)
    initMeasureTool();
    syncMeasureAnnotations();
    // the WebGL canvas has no preserveDrawingBuffer — the smoke test samples
    // pixels from this in-app capture instead of page.screenshot
    (window as { __viewerCapture?: typeof captureViewer }).__viewerCapture = captureViewer;
    const observer = new ResizeObserver(() => {
      window.dispatchEvent(new Event('resize'));
    });
    observer.observe(host.current);
    return () => {
      observer.disconnect();
      unsubscribe();
      detach();
    };
  }, []);

  return <div ref={host} className="absolute inset-0 [&>canvas]:block [&>canvas]:size-full" />;
}
