// Bend sequence animation: the part mesh re-posed per frame through the
// fold coordinates (schema-2 bend_plan arrays), stepping through the best
// plan with the installed punch/die sections and ram/table drawn as
// translucent extrusions in the machine frame. The playhead lives in
// module state (smooth per-frame scrubbing without store round-trips);
// crossing a step boundary writes a viewer param so the paint pass
// rebuilds the overlays for the new step.

import { COL, segmentIdColor } from '../../colorizers/core';
import type { LegendEntry, RGB, ViewMode } from '../../registry/types';
import { useStore } from '../../state/store';
import { latestBendPlan, planField } from './bendplan';
import type { GraphStats } from './foldmath';
import { machinePremultiply, poseVertices } from './foldmath';

const ACTIVE: RGB = [0.95, 0.66, 0.23];
const PENDING: RGB = [0.6, 0.62, 0.66];
const PUNCH: RGB = [0.45, 0.62, 0.85];
const DIE: RGB = [0.55, 0.58, 0.62];
const FRAME: RGB = [0.4, 0.42, 0.46];

const SECONDS_PER_STEP = 3.0;
const SPRINGBACK_FRACTION = 0.9; // stroke to overbend, then relax back

/** Playhead shared between the mode's animator and the Controls panel.
 * pos is step + fraction in [0, steps]. */
export const playhead = {
  pos: 0,
  playing: false,
  speed: 1.0,
  steps: 0,
  version: 0,
  listeners: new Set<() => void>(),
  notify() {
    this.version += 1;
    for (const listener of this.listeners) listener();
  },
};

export function stepOfPos(pos: number, steps: number): number {
  return Math.min(Math.floor(Math.max(pos, 0)), Math.max(steps - 1, 0));
}

/** Move the playhead; a step change re-paints (new overlays). */
export function seekPlayhead(pos: number) {
  const clamped = Math.min(Math.max(pos, 0), playhead.steps || 0);
  const before = stepOfPos(playhead.pos, playhead.steps);
  playhead.pos = clamped;
  const after = stepOfPos(clamped, playhead.steps);
  playhead.notify();
  if (before !== after) {
    useStore.getState().setViewerParam('sheet_metal', 'bendseqStep', after);
  }
}

function phiAt(fraction: number, phiTarget: number, phiRelaxed: number) {
  if (fraction <= SPRINGBACK_FRACTION) {
    return (fraction / SPRINGBACK_FRACTION) * phiTarget;
  }
  const w = (fraction - SPRINGBACK_FRACTION) / (1 - SPRINGBACK_FRACTION);
  return phiTarget + w * (phiRelaxed - phiTarget);
}

function punchShift(thickness: number, phi: number) {
  return (thickness / 2)
    / Math.max(Math.cos(Math.min(Math.abs(phi), 2.6) / 2), 0.2);
}

function sectionSpans(placement: any): [number, number][] {
  const spans: [number, number][] = [];
  for (const run of placement?.runs ?? []) {
    for (const section of run.sections) {
      spans.push([section.x_start, section.x_end]);
    }
  }
  return spans;
}

export const bendSequenceMode: ViewMode = {
  id: 'bend_sequence',
  label: 'Bend sequence (animate)',
  async paint(ctx) {
    const result = latestBendPlan(ctx);
    if (!result) {
      throw new Error('no bend plan result — run sheet_metal/bend_plan first');
    }
    const s = result.stats;
    if (!s.fold_mesh?.available) {
      throw new Error(`fold coordinates unavailable: ${
        s.fold_mesh?.reason ?? 're-run sheet_metal/bend_plan'}`);
    }
    const plan = (s.plans ?? []).find((p: any) => p.feasible);
    if (!plan) {
      throw new Error('no feasible plan — the animation follows the best '
        + 'search plan');
    }
    const graph = s.graph as GraphStats;
    const steps = plan.steps as any[];
    playhead.steps = steps.length;
    playhead.pos = Math.min(Math.max(playhead.pos, 0), steps.length);
    playhead.notify();

    const need = async (name: string) => {
      const desc = planField(ctx, result, name);
      if (!desc) throw new Error(`bend_plan result lacks ${name} — recompute`);
      return ctx.getField(desc);
    };
    const flat = await need('flat_verts') as Float32Array;
    const vertexPanel = await need('vertex_panel') as Uint8Array;
    const vertexBend = await need('vertex_bend') as Uint8Array;
    const panelId = await need('panel_id') as Uint8Array;
    const collisionDesc = planField(ctx, result, 'collision_faces');
    const collision = collisionDesc
      ? await ctx.getField(collisionDesc) as Uint8Array : null;

    // face-level bend ownership (majority of the three corners)
    const faceBend = new Uint8Array(ctx.faceCount);
    for (let f = 0; f < ctx.faceCount; f++) {
      const a = vertexBend[ctx.faces[3 * f]];
      const b = vertexBend[ctx.faces[3 * f + 1]];
      const c = vertexBend[ctx.faces[3 * f + 2]];
      faceBend[f] = (a && (a === b || a === c)) ? a : (b && b === c ? b : 0);
    }

    const stepIndex = stepOfPos(playhead.pos, steps.length);
    const step = steps[stepIndex];
    const primary = graph.bends[step.bend_ids[0]];

    // done / active / pending coloring
    const doneBends = new Set<number>();
    for (let k = 0; k < stepIndex; k++) {
      for (const bendId of steps[k].bend_ids) doneBends.add(bendId);
    }
    const activeBends = new Set<number>(step.bend_ids);
    const donePanels = new Set<number>([graph.base_panel]);
    for (const bendId of doneBends) {
      donePanels.add(graph.bends[bendId].child_panel);
    }
    ctx.paintFaces((f) => {
      if (collision?.[f]) return COL.tip; // mesh-check collision faces (red)
      if (faceBend[f]) {
        const bendId = faceBend[f] - 1;
        if (activeBends.has(bendId)) return ACTIVE;
        return doneBends.has(bendId) ? segmentIdColor(panelId[f]) : PENDING;
      }
      if (!panelId[f]) return COL.inaccess;
      return donePanels.has(panelId[f] - 1)
        ? segmentIdColor(panelId[f]) : PENDING;
    });

    // installed tooling of the step's setup, in the machine frame
    const setup = (plan.setups as any[]).find(
      (x) => x.step_indices.includes(stepIndex));
    const tooling = s.tooling ?? {};
    const thickness = Number(s.thickness);
    let punchHeightOffset = 0;
    const allSpans: [number, number][] = [];
    if (setup) {
      const punch = tooling.punches?.[setup.punch_id];
      const die = tooling.dies?.[setup.die_id];
      if (punch) {
        const punchSpans = sectionSpans(setup.punch);
        allSpans.push(...punchSpans);
        ctx.addOverlayMesh({
          profile: punch.profile, spans: punchSpans, color: PUNCH,
          opacity: 0.5, tag: 'punch',
        });
        const minZ = Math.min(...punch.profile.map(
          (p: [number, number]) => p[1]));
        punchHeightOffset = punch.height + minZ;
        if (tooling.machine?.ram_profile) {
          ctx.addOverlayMesh({
            profile: tooling.machine.ram_profile,
            spans: [[Math.min(...punchSpans.map((x) => x[0])) - 40,
              Math.max(...punchSpans.map((x) => x[1])) + 40]],
            color: FRAME, opacity: 0.25,
            yzOffset: [0, punchHeightOffset], tag: 'punch',
          });
        }
      }
      if (die) {
        const dieSpans = sectionSpans(setup.die);
        allSpans.push(...dieSpans);
        ctx.addOverlayMesh({
          profile: die.profile, spans: dieSpans, color: DIE,
          opacity: 0.5, yzOffset: [0, -thickness / 2],
        });
        if (tooling.machine?.table_profile) {
          ctx.addOverlayMesh({
            profile: tooling.machine.table_profile,
            spans: [[Math.min(...dieSpans.map((x) => x[0])) - 40,
              Math.max(...dieSpans.map((x) => x[1])) + 40]],
            color: FRAME, opacity: 0.25,
            yzOffset: [0, -thickness / 2 - die.height],
          });
        }
      }
    }

    // per-frame pose: scrub-follow, playback advance, springback snap
    const posed = new Float32Array(flat.length);
    const theta = new Float64Array(graph.bends.length);
    const phiRelaxed = Math.abs(primary.angle_relaxed);
    let lastKey = '';
    let lastTime: number | null = null;
    ctx.setAnimator((tMs) => {
      if (playhead.playing && lastTime != null) {
        const dt = (tMs - lastTime) / 1000;
        const next = playhead.pos + (dt * playhead.speed) / SECONDS_PER_STEP;
        if (next >= steps.length) {
          playhead.pos = steps.length;
          playhead.playing = false;
          playhead.notify();
        } else {
          playhead.pos = next;
          if (stepOfPos(next, steps.length) !== stepIndex) {
            lastTime = null;
            playhead.notify();
            useStore.getState().setViewerParam(
              'sheet_metal', 'bendseqStep', stepOfPos(next, steps.length));
            return; // repaint takes over with the next step's overlays
          }
        }
      }
      lastTime = tMs;
      const fraction = Math.min(Math.max(playhead.pos - stepIndex, 0), 1);
      const key = `${fraction.toFixed(5)}|${playhead.playing}`;
      if (key === lastKey) return;
      lastKey = key;
      const phi = phiAt(fraction, step.phi_target, phiRelaxed);
      theta.set(step.theta_before);
      for (const bendId of step.bend_ids) {
        theta[bendId] = Math.sign(graph.bends[bendId].angle_overbend) * phi;
      }
      const machine = machinePremultiply(
        step.placement, step.lift_sign, phi);
      poseVertices(graph, flat, vertexPanel, vertexBend, theta,
        machine, posed);
      ctx.setVertexPositions(posed, !playhead.playing);
      ctx.shiftOverlay('punch', punchShift(thickness, phi)
        - punchShift(thickness, 0));
    });

    const legend: LegendEntry[] = [
      { color: ACTIVE, label: `active bend${step.bend_ids.length > 1 ? 's' : ''} ${step.bend_ids.join(', ')}` },
      { color: PENDING, label: 'pending panels' },
      { color: PUNCH, label: setup ? `punch ${setup.punch_id}` : 'punch' },
      { color: DIE, label: setup ? `die ${setup.die_id}` : 'die' },
      { color: FRAME, label: 'ram / table' },
    ];
    const orientation = `${step.flip ? 'flipped' : 'face up'}, `
      + `${step.rotation ? 'rotated' : 'as laid'}`;
    return {
      legend,
      stats: `step ${stepIndex + 1}/${steps.length} · `
        + `bends [${step.bend_ids.join(', ')}] · `
        + `target ${(Math.abs(primary.angle_target) * 180 / Math.PI).toFixed(0)}° · `
        + `${orientation}`
        + (setup ? ` · ${setup.punch_id} / ${setup.die_id}` : '')
        + ` — scrub or play in the controls panel`,
    };
  },
};
