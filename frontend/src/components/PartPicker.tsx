import { useRef, useState } from 'react';
import { useStore } from '../state/store';
import { selectPart } from '../viewer/controller';
import { uploadAndSelect } from '../viewer/jobs';

export function PartPicker() {
  const parts = useStore((s) => s.parts);
  const partId = useStore((s) => s.partId);
  const fileInput = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  async function onUpload(file: File) {
    setBusy(true);
    try {
      await uploadAndSelect(file);
    } catch (err) {
      useStore.getState().set({ error: String(err) });
    } finally {
      setBusy(false);
      if (fileInput.current) fileInput.current.value = '';
    }
  }

  // ids are content hashes: same display name can mean different geometry
  const nameCounts = new Map<string, number>();
  for (const p of parts) nameCounts.set(p.name, (nameCounts.get(p.name) ?? 0) + 1);

  return (
    <>
      <label>Part</label>
      <div className="row">
        <select
          value={partId ?? ''}
          onChange={(e) => void selectPart(e.target.value)}
        >
          {!partId && <option value="">— pick a part —</option>}
          {parts.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
              {(nameCounts.get(p.name) ?? 0) > 1 ? ` · ${p.id.slice(0, 6)}` : ''}
              {p.status === 'raw' ? ' (not meshed)' : ''}
              {p.status === 'preview' ? ' (preview)' : ''}
            </option>
          ))}
        </select>
        <button
          type="button" className="upload" disabled={busy}
          onClick={() => fileInput.current?.click()}
        >
          {busy ? '…' : '+ file'}
        </button>
      </div>
      <input
        ref={fileInput} type="file" accept=".stl,.stp,.step" hidden
        onChange={(e) => e.target.files?.[0] && void onUpload(e.target.files[0])}
      />
    </>
  );
}
