// Materials and shader patches for the composable render layers: the neutral
// base/lens Phong pair (with the barycentric triangle-edge overlay compiled
// in, gated by a uniform), and the X-ray Fresnel shell. Kept out of scene.ts
// so the scene reads as layer plumbing, not GLSL.

import * as THREE from 'three';

/** Theme-matched colours for the neutral layers (the lens paints its own). */
export interface ThemeColors {
  /** Unpainted base mesh. */
  base: number;
  /** Barycentric triangle-edge lines (shader overlay). */
  triEdge: number;
  /** BREP boundary polylines. */
  brepEdge: number;
  xrayBase: number;
  xrayRim: number;
}

export const THEME_COLORS: Record<'light' | 'dark', ThemeColors> = {
  dark: {
    base: 0x969ca4, triEdge: 0x14181d, brepEdge: 0xdfe3e8,
    xrayBase: 0x3d4654, xrayRim: 0xc2d6ea,
  },
  light: {
    base: 0xc6cbd2, triEdge: 0x30353c, brepEdge: 0x33373d,
    xrayBase: 0x93a0b0, xrayRim: 0x2a4568,
  },
};

/** Uniforms shared by every edge-patched material, so one write flips the
 * triangle-edge overlay everywhere. */
export interface EdgeUniforms {
  uEdges: { value: number };
  uEdgeColor: { value: THREE.Color };
}

export function makeEdgeUniforms(): EdgeUniforms {
  return {
    uEdges: { value: 0 },
    uEdgeColor: { value: new THREE.Color(THEME_COLORS.dark.triEdge) },
  };
}

/**
 * Compile the barycentric triangle-edge overlay into a built-in material:
 * an `aBary` attribute carries (1,0,0)/(0,1,0)/(0,0,1) per corner, and the
 * fragment stage darkens pixels near a zero barycentric coordinate —
 * crisp constant-width wireframe lines with no extra geometry, correct
 * under posing (unlike a prebuilt WireframeGeometry).
 */
export function patchEdgeShader(
  material: THREE.Material, uniforms: EdgeUniforms,
) {
  material.onBeforeCompile = (shader) => {
    shader.uniforms.uEdges = uniforms.uEdges;
    shader.uniforms.uEdgeColor = uniforms.uEdgeColor;
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>',
        '#include <common>\nattribute vec3 aBary;\nvarying vec3 vBary;')
      .replace('#include <begin_vertex>',
        '#include <begin_vertex>\nvBary = aBary;');
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>',
        '#include <common>\nuniform float uEdges;\nuniform vec3 uEdgeColor;\nvarying vec3 vBary;')
      .replace('#include <dithering_fragment>', `#include <dithering_fragment>
        if (uEdges > 0.5) {
          vec3 fw = fwidth(vBary);
          vec3 s = smoothstep(vec3(0.0), fw * 1.2, vBary);
          float edge = 1.0 - min(min(s.x, s.y), s.z);
          gl_FragColor.rgb = mix(gl_FragColor.rgb, uEdgeColor, edge * 0.85);
        }`);
  };
}

/** Uniforms carrying the independent lens/findings opacities. The lens
 * colour attribute's ALPHA channel is a static 0/1 findings mask; the
 * fragment stage mixes between these two opacities on it — a slider drag
 * is two float writes, no 2.4M-corner buffer rewrite. */
export interface LensOpacityUniforms {
  uLensOpacity: { value: number };
  uFindingsOpacity: { value: number };
}

export function makeLensOpacityUniforms(): LensOpacityUniforms {
  return { uLensOpacity: { value: 1 }, uFindingsOpacity: { value: 1 } };
}

/** Chainable shader patch (after the edge patch): replace the vertex-alpha
 * product with the lens/findings mix keyed on the mask in vColor.a. */
export function patchLensOpacity(
  material: THREE.Material, uniforms: LensOpacityUniforms,
) {
  const previous = material.onBeforeCompile;
  material.onBeforeCompile = (shader, renderer) => {
    previous?.(shader, renderer);
    shader.uniforms.uLensOpacity = uniforms.uLensOpacity;
    shader.uniforms.uFindingsOpacity = uniforms.uFindingsOpacity;
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>',
        '#include <common>\nuniform float uLensOpacity;\nuniform float uFindingsOpacity;')
      .replace('#include <color_fragment>', `#include <color_fragment>
        diffuseColor.a = opacity
          * mix(uLensOpacity, uFindingsOpacity, vColor.a);`);
  };
}

/** The repeating (1,0,0)/(0,1,0)/(0,0,1) corner pattern for `aBary`. */
export function makeBaryAttribute(faceCount: number): THREE.BufferAttribute {
  const bary = new Float32Array(faceCount * 9);
  for (let f = 0; f < faceCount; f++) {
    bary[9 * f] = 1;
    bary[9 * f + 4] = 1;
    bary[9 * f + 8] = 1;
  }
  return new THREE.BufferAttribute(bary, 3);
}

// polygon fills sit slightly behind their true depth so BREP edge lines and
// measurement annotations at true depth never stitch. Base, lens, selection
// and the X-ray depth prepass all share the SAME offset — the lens overlay
// relies on bit-identical depth values across the layers.
export const SURFACE_OFFSET = { factor: 1, units: 1 } as const;

function withSurfaceOffset<T extends THREE.Material>(material: T): T {
  material.polygonOffset = true;
  material.polygonOffsetFactor = SURFACE_OFFSET.factor;
  material.polygonOffsetUnits = SURFACE_OFFSET.units;
  return material;
}

/** Neutral opaque base: what you see where no lens colour applies. */
export function makeBaseMaterial(edges: EdgeUniforms): THREE.MeshPhongMaterial {
  const material = withSurfaceOffset(new THREE.MeshPhongMaterial({
    color: THEME_COLORS.dark.base, specular: 0x111111, shininess: 18,
  }));
  patchEdgeShader(material, edges);
  return material;
}

/** The lens layer: vertex RGBA colours (alpha drives "findings only"),
 * always transparent so per-face alpha works, never writing depth — it
 * shades exactly on top of the base surface (identical depth values). */
export function makeLensMaterial(edges: EdgeUniforms): THREE.MeshPhongMaterial {
  const material = withSurfaceOffset(new THREE.MeshPhongMaterial({
    vertexColors: true, specular: 0x111111, shininess: 18,
    transparent: true, depthWrite: false,
  }));
  patchEdgeShader(material, edges);
  return material;
}

/** X-ray hidden pass: the same lens geometry drawn faintly where it is
 * OCCLUDED (depth test inverted against the depth prepass). */
export function makeLensOccludedMaterial(): THREE.MeshPhongMaterial {
  return withSurfaceOffset(new THREE.MeshPhongMaterial({
    vertexColors: true, specular: 0x000000, shininess: 0,
    transparent: true, depthWrite: false, depthFunc: THREE.GreaterDepth,
  }));
}

/** Selection layer: opaque where selected (alpha 1), discarded elsewhere
 * (alpha 0 + alphaTest) — survives ghosting because it writes depth. */
export function makeSelectionMaterial(edges: EdgeUniforms): THREE.MeshPhongMaterial {
  const material = withSurfaceOffset(new THREE.MeshPhongMaterial({
    vertexColors: true, specular: 0x111111, shininess: 18,
    alphaTest: 0.5,
  }));
  patchEdgeShader(material, edges);
  return material;
}

/** X-ray depth prepass: fills the depth buffer invisibly so the transparent
 * Fresnel shell and the lens visible/occluded split classify correctly. */
export function makeDepthPrepassMaterial(): THREE.MeshBasicMaterial {
  return withSurfaceOffset(new THREE.MeshBasicMaterial({ colorWrite: false }));
}

/** The X-ray shell: a subdued transparent base with camera-normal (Fresnel)
 * rim lighting, so silhouettes read while the interior stays see-through.
 * Written with the clipping chunks so the section plane applies. */
export function makeXrayMaterial(): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    clipping: true,
    uniforms: {
      uBase: { value: new THREE.Color(THEME_COLORS.dark.xrayBase) },
      uRim: { value: new THREE.Color(THEME_COLORS.dark.xrayRim) },
      uOpacity: { value: 0.13 },
    },
    vertexShader: `
      #include <common>
      #include <clipping_planes_pars_vertex>
      varying vec3 vNormal;
      varying vec3 vViewDir;
      void main() {
        #include <begin_vertex>
        #include <project_vertex>
        #include <clipping_planes_vertex>
        vNormal = normalize(normalMatrix * normal);
        vViewDir = normalize(-mvPosition.xyz);
      }`,
    fragmentShader: `
      #include <common>
      #include <clipping_planes_pars_fragment>
      uniform vec3 uBase;
      uniform vec3 uRim;
      uniform float uOpacity;
      varying vec3 vNormal;
      varying vec3 vViewDir;
      void main() {
        #include <clipping_planes_fragment>
        float rim = pow(1.0 - abs(dot(normalize(vNormal), normalize(vViewDir))), 2.5);
        gl_FragColor = vec4(mix(uBase, uRim, rim), uOpacity + rim * 0.5);
      }`,
  });
}
