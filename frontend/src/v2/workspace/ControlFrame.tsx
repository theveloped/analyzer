import type { PmiDimension, PmiTolerance } from '../../api/types';

/**
 * Feature-control-frame + FOS rendering for the PMI panel. Glyphs are the
 * official GD&T Unicode characters; the type keys are the OCP
 * XCAFDimTolObjects_GeomToleranceType_* enum names written into pmi.json.
 * Unknown types fall back to their text label.
 */

// characteristic → glyph (Unicode point in the comment)
const GDT_SYMBOL: Record<string, string> = {
  Straightness: '⏤',            // ⏤
  Flatness: '⏥',                // ⏥
  CircularityOrRoundness: '○',  // ○
  Cylindricity: '⌭',            // ⌭
  ProfileOfLine: '⌒',           // ⌒
  ProfileOfSurface: '⌓',        // ⌓
  Angularity: '∠',              // ∠
  Perpendicularity: '⟂',        // ⟂
  Parallelism: '∥',             // ∥
  Position: '⌖',                // ⌖
  Concentricity: '◎',           // ◎
  Coaxiality: '◎',              // ◎ (coaxiality shares the concentricity symbol)
  Symmetry: '⌯',                // ⌯
  CircularRunout: '↗',          // ↗
  TotalRunout: '⌰',             // ⌰
};

const DIAMETER = '⌀';           // ⌀
const MAT_SYMBOL: Record<string, string> = { M: 'Ⓜ', L: 'Ⓛ', S: 'Ⓢ' }; // Ⓜ Ⓛ Ⓢ
// geometric-tolerance / datum modifier name → glyph (else rendered as text)
const MOD_SYMBOL: Record<string, string> = {
  Free_State: 'Ⓕ', FreeState: 'Ⓕ',                    // Ⓕ
  Tangent_Plane: 'Ⓣ',                                      // Ⓣ
  Maximum_Material_Requirement: 'Ⓜ', MaximumMaterialRequirement: 'Ⓜ',
  Least_Material_Requirement: 'Ⓛ', LeastMaterialRequirement: 'Ⓛ',
};
const ZONE_SYMBOL: Record<string, string> = { Projected: 'Ⓟ' }; // Ⓟ

const cell = 'flex items-center justify-center px-1.5 py-0.5 min-w-[1.5rem] text-center';
const box = 'inline-flex items-stretch divide-x divide-zinc-500/60 rounded-sm border border-zinc-500/60 text-sm font-medium text-zinc-800 dark:text-zinc-100 leading-none';

function modGlyphs(mods: string[] | undefined): string {
  return (mods ?? []).map((m) => MOD_SYMBOL[m] ?? '').join('');
}

/** A boxed feature control frame, e.g. │ ⌖ │ ⌀0.2Ⓜ │ A │ B │ C │ */
export function ToleranceFrame({ t }: { t: PmiTolerance }) {
  const sym = (t.type && GDT_SYMBOL[t.type]) || null;
  const dia = t.type_of_value === 'Diameter' ? DIAMETER : '';
  const mat = t.material_modifier ? (MAT_SYMBOL[t.material_modifier] ?? '') : '';
  const zone = t.zone_modifier ? (ZONE_SYMBOL[t.zone_modifier] ?? '') : '';
  const val = t.value ? formatNum(t.value) : '';   // blank an absent/0 magnitude
  const extraMods = modGlyphs(t.modifiers);
  const datumRefs = (t.datum_refs ?? []).filter((r) => r.name);
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className={box}>
        <span className={cell} title={t.type ?? undefined}>
          {sym ?? <span className="text-xs">{t.type}</span>}
        </span>
        <span className={cell}>
          {dia}{val}{mat}{zone}
        </span>
        {datumRefs.map((r, i) => (
          <span key={i} className={cell} title={`datum ${r.name}${r.modifiers?.length ? ' ' + r.modifiers.join(',') : ''}`}>
            {r.name}{modGlyphs(r.modifiers)}
          </span>
        ))}
      </span>
      {extraMods && <span className="text-sm text-zinc-500 dark:text-zinc-400">{extraMods}</span>}
      {(t.modifiers ?? []).includes('All_Around') && (
        <span className="text-[10px] uppercase tracking-wide text-zinc-400">all around</span>
      )}
    </div>
  );
}

/** A feature-of-size callout, e.g. ⌀12 ±0.05 or 60° Max */
export function DimensionCallout({ d }: { d: PmiDimension }) {
  const dia = d.type && d.type.includes('Diameter') ? DIAMETER : '';
  const unit = d.angular ? '°' : '';
  const tol = d.upper_tolerance != null && d.lower_tolerance != null
    ? symmetric(d.upper_tolerance, d.lower_tolerance)
    : null;
  return (
    <div className="flex flex-wrap items-baseline gap-1.5 text-sm text-zinc-800 dark:text-zinc-100">
      <span className="font-medium">{dia}{formatNum(d.value)}{unit}</span>
      {tol && <span className="text-zinc-500 dark:text-zinc-400">{tol}</span>}
      {d.qualifier && <span className="text-[10px] uppercase tracking-wide text-zinc-400">{d.qualifier}</span>}
    </div>
  );
}

function symmetric(up: number, lo: number): string {
  if (up === -lo) return `±${formatNum(up)}`;
  const u = up >= 0 ? `+${formatNum(up)}` : formatNum(up);
  const l = lo >= 0 ? `+${formatNum(lo)}` : formatNum(lo);
  return `${u} / ${l}`;
}

function formatNum(n: number): string {
  if (!isFinite(n)) return String(n);
  const r = Math.round(n * 1000) / 1000;
  return Number.isInteger(r) ? String(r) : String(r);
}
