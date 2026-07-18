import { ChevronDown, Play, RotateCw, Settings2, Sparkles } from 'lucide-react';
import { useState } from 'react';
import { useStore } from '../../state/store';
import type { Analysis, ComputeField } from '../analyses';
import { Button } from '../components/ui/button';
import { StatusBadge } from '../components/ui/status';
import {
  Collapsible, CollapsibleContent, CollapsibleTrigger,
} from '../components/ui/collapsible';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Switch } from '../components/ui/switch';
import { cn } from '../lib/utils';
import { useV2 } from '../store';
import { resultFor, useActiveAnalysis } from './hooks';
import { runAnalysis, useBusy } from './run';

/** One engineer-set threshold field (client-side, instant recolor). */
function ThresholdField({ a }: { a: Analysis }) {
  const params = useStore((s) => s.viewerParams[a.process]) ?? {};
  const setParam = useStore((s) => s.setViewerParam);
  const value = params[a.thresholdParam] ?? a.thresholdDefault;
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor="threshold">{a.thresholdLabel}</Label>
      <div className="flex items-center gap-2">
        <Input
          id="threshold"
          type="number"
          step="0.1"
          value={String(value)}
          onChange={(e) => setParam(a.process, a.thresholdParam, e.target.value)}
        />
        <span className="w-8 text-xs text-muted-foreground">{a.unit}</span>
      </div>
      <p className="text-xs text-muted-foreground">
        Faces past this limit are flagged. Adjusts instantly — no recompute.
      </p>
    </div>
  );
}

function ComputeInput({ a, field }: { a: Analysis; field: ComputeField }) {
  const value = useV2((s) => s.compute[a.id]?.[field.key]);
  const setCompute = useV2((s) => s.setCompute);
  if (field.type === 'bool') {
    return (
      <div className="flex items-center justify-between gap-2">
        <div>
          <Label>{field.label}</Label>
          {field.hint && <p className="text-[11px] text-muted-foreground">{field.hint}</p>}
        </div>
        <Switch
          checked={value === true}
          onCheckedChange={(v) => setCompute(a.id, field.key, v)}
          aria-label={field.label}
        />
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-1">
      <Label>{field.label}{field.unit ? ` (${field.unit})` : ''}</Label>
      <Input
        type="number"
        step="0.1"
        placeholder={field.placeholder}
        value={value == null ? '' : String(value)}
        onChange={(e) => {
          const raw = e.target.value;
          setCompute(a.id, field.key, raw === '' ? null : Number(raw));
        }}
      />
      {field.hint && <p className="text-[11px] text-muted-foreground">{field.hint}</p>}
    </div>
  );
}

/** Heatmap-scale + edge-mask knobs (viewer-side, thickness/gaps only). */
function DisplayAdvanced({ a }: { a: Analysis }) {
  const params = useStore((s) => s.viewerParams[a.process]) ?? {};
  const setParam = useStore((s) => s.setViewerParam);
  const isSphere = a.id === 'thickness' || a.id === 'gaps';
  return (
    <>
      <div className="flex flex-col gap-1">
        <Label>{a.scaleLabel} ({a.unit})</Label>
        <Input
          type="number"
          step="0.1"
          placeholder="auto"
          value={params[a.scaleParam] == null ? '' : String(params[a.scaleParam])}
          onChange={(e) => setParam(a.process, a.scaleParam, e.target.value)}
        />
      </div>
      {isSphere && (
        <div className="flex items-center justify-between gap-2">
          <div>
            <Label>Hide edge artifacts</Label>
            <p className="text-[11px] text-muted-foreground">
              Show readings explained by sharp edges as OK.
            </p>
          </div>
          <Switch
            checked={params.maskExplained !== false}
            onCheckedChange={(v) => setParam(a.process, 'maskExplained', v)}
            aria-label="Hide edge artifacts"
          />
        </div>
      )}
    </>
  );
}

export function SettingsRail() {
  const a = useActiveAnalysis();
  const globalAdvanced = useV2((s) => s.advanced);
  const [open, setOpen] = useState(globalAdvanced);
  const manifest = useStore((s) => s.manifest);
  const stats = useStore((s) => s.stats);
  const error = useStore((s) => s.error);
  const meshReady = useStore((s) => s.meshReady);
  const busy = useBusy();
  const computed = !!resultFor(manifest, a);

  return (
    <div className="flex h-full w-72 shrink-0 flex-col gap-3 overflow-auto border-l bg-muted/20 p-4">
      <div>
        <div className="flex items-center gap-2">
          <a.icon className="size-4 text-primary" />
          <h2 className="text-sm font-semibold">{a.label}</h2>
          {computed ? (
            <StatusBadge status="good">computed</StatusBadge>
          ) : (
            <StatusBadge status="neutral">not run</StatusBadge>
          )}
        </div>
        <p className="mt-1 text-xs text-muted-foreground">{a.blurb}</p>
      </div>

      <ThresholdField a={a} />

      <Button
        onClick={() => runAnalysis(a)}
        disabled={!meshReady || busy}
        className="w-full"
      >
        {busy ? (
          <><RotateCw className="size-4 animate-spin" /> Running…</>
        ) : computed ? (
          <><RotateCw className="size-4" /> Re-run check</>
        ) : (
          <><Play className="size-4" /> Run check</>
        )}
      </Button>

      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className="flex w-full items-center justify-between rounded-md px-1 py-1 text-xs font-medium text-muted-foreground hover:text-foreground"
          >
            <span className="flex items-center gap-1.5">
              <Settings2 className="size-3.5" /> Advanced settings
            </span>
            <ChevronDown className={cn('size-3.5 transition-transform', open && 'rotate-180')} />
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent className="flex flex-col gap-3 pt-2">
          <div className="rounded-md border border-dashed bg-background/60 p-2 text-[11px] text-muted-foreground">
            <Sparkles className="mr-1 inline size-3" />
            These are set correctly by default — change only if you know the
            part geometry. Compute knobs re-run the check.
          </div>
          <DisplayAdvanced a={a} />
          <div className="h-px bg-border" />
          {a.advancedFields.map((field) => (
            <ComputeInput key={field.key} a={a} field={field} />
          ))}
        </CollapsibleContent>
      </Collapsible>

      <div className="mt-1 h-px bg-border" />

      <div>
        <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          Findings
        </div>
        {error ? (
          <p className="whitespace-pre-wrap text-xs text-destructive">⚠ {error}</p>
        ) : stats ? (
          <p className="whitespace-pre-wrap text-xs text-muted-foreground">{stats}</p>
        ) : (
          <p className="text-xs text-muted-foreground">
            {computed ? 'Adjust the limit or inspect faces in the viewer.' : 'Run the check to see findings.'}
          </p>
        )}
      </div>
    </div>
  );
}
