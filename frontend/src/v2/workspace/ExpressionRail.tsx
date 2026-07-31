import clsx from 'clsx';
import { X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Button } from '../../catalyst/button';
import { Input } from '../../catalyst/input';
import { Select } from '../../catalyst/select';
import type { CheckSource, RouteCheck } from '../../api/types';
import {
  expressionFields, ruleText, type ExprAggregate, type ExprTerm,
  type FieldOption, type TermOp, type TermRule,
} from '../../fields/expression';
import { BOUND_UNITS, type BandBound } from '../../fields/stats';
import { useStore } from '../../state/store';
import { EXPRESSION_LENS, sourceHashes } from '../checks/catalog';
import { resolveTerms } from '../../fields/expression';
import { hintCls, labelCls } from '../components/styles';
import { useV2 } from '../store';
import { storeRoute, useRouteSection } from './hooks';

/**
 * Build one expression check: a list of terms over stored fields, joined by
 * and / or / and-not, with an area limit that turns the result into a verdict.
 *
 * The field list is a VIEW OVER WHATEVER THE CACHE HOLDS — the same rule the
 * study table follows. You compose out of what has been computed, and a field
 * you want that is not there yet is one lens click away. Inventing a second
 * catalogue of nameable fields would just give it something to drift from.
 *
 * Every edit repaints the lens, so the mask you are describing is on screen
 * while you describe it.
 */

const OPS: { id: TermOp; label: string }[] = [
  { id: 'and', label: 'and' },
  { id: 'or', label: 'or' },
  { id: 'andNot', label: 'and not' },
];

const emptyBound = (): BandBound => ({ value: '', unit: 'abs' });

function defaultRule(option: FieldOption): TermRule {
  if (option.rule === 'band') return { kind: 'band', lo: emptyBound(), hi: emptyBound() };
  if (option.rule === 'mask') return { kind: 'mask' };
  return { kind: 'category', values: [] };
}

/** A term keeps (source, member) — never the result hash, which moves on
 * every re-run. The source is created on demand from the browsed field. */
function sourceIdFor(option: FieldOption): string {
  return `${option.process}.${option.analysis}`;
}

function BoundRow({ label, bound, unit, onChange }: {
  label: string; bound: BandBound; unit: string;
  onChange: (next: BandBound) => void;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="w-8 shrink-0 text-[11px]/5 text-zinc-500 dark:text-zinc-400">
        {label}
      </span>
      <Input
        value={bound.value}
        onChange={(e) => onChange({ ...bound, value: e.target.value })}
        placeholder="—"
        aria-label={`${label} value`}
        className="min-w-0 flex-1"
      />
      <Select
        value={bound.unit}
        onChange={(e) => onChange({ ...bound, unit: e.target.value as BandBound['unit'] })}
        aria-label={`${label} unit`}
        className="w-32 shrink-0"
      >
        {BOUND_UNITS(unit).map((u) => (
          <option key={u.id} value={u.id}>{u.label}</option>
        ))}
      </Select>
    </div>
  );
}

function TermCard({ term, index, onChange, onRemove }: {
  term: ExprTerm; index: number;
  onChange: (next: ExprTerm) => void; onRemove: () => void;
}) {
  const rule = term.rule;
  return (
    <div className="rounded-lg border border-zinc-950/10 p-2 dark:border-white/10">
      <div className="flex items-center gap-1.5">
        {index === 0 ? (
          <span className="w-16 shrink-0 text-[11px]/5 font-medium text-zinc-400">
            where
          </span>
        ) : (
          <Select
            value={term.op}
            onChange={(e) => onChange({ ...term, op: e.target.value as TermOp })}
            aria-label="combine with"
            className="w-16 shrink-0"
          >
            {OPS.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
          </Select>
        )}
        <span className="min-w-0 flex-1 truncate text-xs/5 font-medium text-zinc-950 dark:text-white"
          title={term.label ?? term.field}
        >
          {term.label ?? term.field}
        </span>
        <button
          type="button" onClick={onRemove} title="Remove this term"
          className="rounded p-0.5 text-zinc-400 hover:bg-zinc-950/10 hover:text-zinc-700 dark:hover:bg-white/10 dark:hover:text-zinc-200"
        >
          <X className="size-3" />
        </button>
      </div>

      <div className="mt-1.5 flex flex-col gap-1">
        {rule.kind === 'band' && (
          <>
            <BoundRow
              label="from" bound={rule.lo} unit={term.unit ?? ''}
              onChange={(lo) => onChange({ ...term, rule: { ...rule, lo } })}
            />
            <BoundRow
              label="to" bound={rule.hi} unit={term.unit ?? ''}
              onChange={(hi) => onChange({ ...term, rule: { ...rule, hi } })}
            />
          </>
        )}
        {rule.kind === 'mask' && (
          <label className="flex items-center gap-1.5 text-[11px]/5 text-zinc-500 dark:text-zinc-400">
            <input
              type="checkbox" checked={!!rule.negate}
              onChange={(e) => onChange({ ...term, rule: { ...rule, negate: e.target.checked } })}
            />
            invert (faces where it is NOT set)
          </label>
        )}
        {rule.kind === 'category' && (
          <>
            <Input
              value={rule.values.join(', ')}
              onChange={(e) => onChange({
                ...term,
                rule: {
                  ...rule,
                  values: e.target.value.split(',')
                    .map((v) => parseInt(v.trim(), 10))
                    .filter((v) => Number.isFinite(v)),
                },
              })}
              placeholder="category values, e.g. 1, 2"
              aria-label="category values"
            />
            <label className="flex items-center gap-1.5 text-[11px]/5 text-zinc-500 dark:text-zinc-400">
              <input
                type="checkbox" checked={!!rule.negate}
                onChange={(e) => onChange({ ...term, rule: { ...rule, negate: e.target.checked } })}
              />
              invert (everything except those)
            </label>
          </>
        )}
        <div className={hintCls}>{ruleText(term)}</div>
      </div>
    </div>
  );
}

export function ExpressionRail() {
  const manifest = useStore((s) => s.manifest);
  const section = useRouteSection();
  const editing = useV2((s) => s.expressionCheckId);
  const closeBuilder = useV2((s) => s.setExpressionCheckId);

  const stored = section?.route.checks.find((c) => c.id === editing) ?? null;
  const options = expressionFields(manifest);

  const [label, setLabel] = useState('');
  const [terms, setTerms] = useState<ExprTerm[]>([]);
  const [aggregate, setAggregate] = useState<ExprAggregate>(
    { limit: 0, severity: 'review' });
  const [picking, setPicking] = useState('');

  // seed from the stored check whenever the builder opens on a different one
  useEffect(() => {
    setLabel(stored?.label ?? 'Expression');
    setTerms(((stored?.policy?.terms ?? []) as ExprTerm[]).map((t) => ({ ...t })));
    setAggregate((stored?.policy?.aggregate as ExprAggregate)
      ?? { limit: 0, severity: 'review' });
  }, [editing]);

  // paint what the terms currently describe, so the rule is visible while
  // it is being written rather than only after it is saved
  useEffect(() => {
    if (!terms.length || !stored) return;
    const status = section?.checks[stored.id];
    const store = useStore.getState();
    const { resolved } = resolveTerms(terms, sourceHashes(
      { ...stored, sources: sourcesFor(terms, options) } as RouteCheck, status));
    store.setViewerParam(EXPRESSION_LENS[0], 'exprTerms', resolved);
    store.set({ processId: EXPRESSION_LENS[0], modeId: EXPRESSION_LENS[1] });
  }, [JSON.stringify(terms)]);

  if (!stored || !section) return null;

  function addTerm(fieldId: string) {
    const option = options.find((o) => o.id === fieldId);
    if (!option) return;
    setTerms((prev) => [...prev, {
      source: sourceIdFor(option),
      field: option.member,
      rule: defaultRule(option),
      op: 'and',
      unit: option.unit,
      label: option.label,
    }]);
    setPicking('');
  }

  async function save() {
    const sources = sourcesFor(terms, options);
    const next: RouteCheck = {
      id: stored!.id,
      label,
      sources,
      policy: { kind: 'expression', terms, aggregate },
      lens: `${EXPRESSION_LENS[0]}:${EXPRESSION_LENS[1]}`,
      ...(stored!.operation ? { operation: stored!.operation } : {}),
    };
    await storeRoute({
      ...section!.route,
      checks: section!.route.checks.map((c) => (c.id === next.id ? next : c)),
    }, section!.route.revision);
  }

  return (
    <div className="flex min-h-full flex-col gap-3 p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-sm/6 font-semibold text-zinc-950 dark:text-white">
            Expression check
          </h2>
          <p className={clsx('mt-1', hintCls)}>
            Turn fields into yes/no regions and combine them. The viewer paints
            the result as you edit.
          </p>
        </div>
        <Button plain onClick={() => closeBuilder(null)} aria-label="Close the builder">
          <X data-slot="icon" />
        </Button>
      </div>

      <div>
        <div className={labelCls}>Name</div>
        <Input value={label} onChange={(e) => setLabel(e.target.value)}
          aria-label="check name" />
      </div>

      <div className="flex flex-col gap-1.5">
        {terms.map((term, i) => (
          <TermCard
            key={i}
            term={term} index={i}
            onChange={(next) => setTerms((prev) =>
              prev.map((t, j) => (j === i ? next : t)))}
            onRemove={() => setTerms((prev) => prev.filter((_, j) => j !== i))}
          />
        ))}
        {!terms.length && (
          <p className={hintCls}>
            No terms yet. Pick a computed field below — anything a lens has
            already produced can be interpreted here.
          </p>
        )}
      </div>

      <div>
        <div className={labelCls}>Add a field</div>
        <Select value={picking} onChange={(e) => addTerm(e.target.value)}
          aria-label="add a field">
          <option value="">
            {options.length ? 'pick a computed field…' : 'nothing computed yet'}
          </option>
          {options.map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}{o.stale ? ' (stale)' : ''} · {o.rule}
            </option>
          ))}
        </Select>
      </div>

      <div>
        <div className={labelCls}>Flag when the selected area exceeds</div>
        <div className="flex items-center gap-1.5">
          <Input
            type="number" min="0" max="100"
            value={String(100 * aggregate.limit)}
            onChange={(e) => setAggregate({
              ...aggregate,
              limit: Math.max(0, (parseFloat(e.target.value) || 0) / 100),
            })}
            aria-label="area limit"
            className="min-w-0 flex-1"
          />
          <span className="shrink-0 text-[11px]/5 text-zinc-500 dark:text-zinc-400">
            % of the part
          </span>
          <Select
            value={aggregate.severity}
            onChange={(e) => setAggregate({
              ...aggregate, severity: e.target.value as ExprAggregate['severity'],
            })}
            aria-label="severity"
            className="w-24 shrink-0"
          >
            <option value="review">review</option>
            <option value="fail">fail</option>
          </Select>
        </div>
        <p className={clsx('mt-1', hintCls)}>
          0 % means any selected face at all is a finding.
        </p>
      </div>

      <div className="mt-auto flex gap-1.5 pt-2">
        <Button outline onClick={() => closeBuilder(null)} className="flex-1">
          Close
        </Button>
        <Button onClick={() => void save()} className="flex-1"
          disabled={!terms.length}>
          Save
        </Button>
      </div>
    </div>
  );
}

/** The `sources` a term list needs: one per (process, analysis) it touches,
 * carrying the params of the result the field was browsed from. Deriving this
 * from the terms — rather than asking the user — is what keeps the check's
 * cache identity and its expression from drifting apart. */
export function sourcesFor(
  terms: ExprTerm[], options: FieldOption[],
): CheckSource[] {
  const out = new Map<string, CheckSource>();
  for (const term of terms) {
    if (out.has(term.source)) continue;
    const option = options.find(
      (o) => sourceIdFor(o) === term.source && o.member === term.field);
    if (!option) continue;
    out.set(term.source, {
      id: term.source,
      analysis: `${option.process}/${option.analysis}`,
      params: option.params,
    });
  }
  return [...out.values()];
}
