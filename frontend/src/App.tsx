import { useEffect, useRef } from 'react';
import { AnalysisPanel } from './components/AnalysisPanel';
import { PartPicker } from './components/PartPicker';
import { InspectPanel, Legend, StatsBar } from './components/Readouts';
import { PROCESS_PLUGINS, getPlugin } from './registry';
import { useStore } from './state/store';
import { attach } from './viewer/controller';

function ProcessTabs() {
  const processId = useStore((s) => s.processId);
  const set = useStore((s) => s.set);
  return (
    <div className="tabs">
      {Object.values(PROCESS_PLUGINS).map((plugin) => (
        <button
          key={plugin.processId}
          type="button"
          className={plugin.processId === processId ? 'active' : ''}
          onClick={() => set({ processId: plugin.processId, modeId: plugin.modes[0].id })}
        >
          {plugin.label}
        </button>
      ))}
    </div>
  );
}

function ModeSelect() {
  const processId = useStore((s) => s.processId);
  const modeId = useStore((s) => s.modeId);
  const set = useStore((s) => s.set);
  const plugin = getPlugin(processId);
  if (!plugin) return null;
  const current = plugin.modes.find((m) => m.id === modeId) ?? plugin.modes[0];
  return (
    <>
      <label>View</label>
      <select value={current.id} onChange={(e) => set({ modeId: e.target.value })}>
        {plugin.modes.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
      </select>
    </>
  );
}

function PluginControls() {
  const processId = useStore((s) => s.processId);
  const plugin = getPlugin(processId);
  if (!plugin?.Controls) return null;
  const Controls = plugin.Controls;
  return <Controls />;
}

export default function App() {
  const canvasHost = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!canvasHost.current) return;
    return attach(canvasHost.current);
  }, []);

  return (
    <div className="app">
      <div className="canvas-host" ref={canvasHost} />
      <div className="panel">
        <h1>DFM analyzer</h1>
        <PartPicker />
        <ProcessTabs />
        <ModeSelect />
        <PluginControls />
        <Legend />
        <StatsBar />
        <InspectPanel />
        <AnalysisPanel />
      </div>
    </div>
  );
}
