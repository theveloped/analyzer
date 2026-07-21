// Plain three.js scene: one un-indexed mesh with per-vertex colors written
// imperatively (three vertices per face, so faceIndex * 3 addresses a face's
// corners — the same contract the old viewer relied on).

import * as THREE from 'three';
import { ViewHelper } from 'three/addons/helpers/ViewHelper.js';
import type { RGB } from '../registry/types';
import { CameraRig } from './cameraRig';
import type { MeasurePick } from './measure';
import {
  makeBaryAttribute, makeBaseMaterial, makeDepthPrepassMaterial,
  makeEdgeUniforms, makeLensMaterial, makeLensOccludedMaterial,
  makeSelectionMaterial, makeXrayMaterial, THEME_COLORS,
} from './styles';
import { DEFAULT_VIEWPORT, type Projection, type ViewportState } from './viewportState';

const GHOST_ALPHA = 0.15;

export class Scene3D {
  private scene = new THREE.Scene();
  private rig: CameraRig;
  private renderer: THREE.WebGLRenderer;
  // ---- composable render layers over ONE set of shared buffer attributes.
  // `mesh` is the opaque base layer and the raycast/bbox anchor; the lens
  // layer carries the vertex colours every mode paints; selection renders
  // the legend-group selection for ghost/isolate; the prepass and occluded
  // meshes exist for the X-ray two-pass split. All layers share position/
  // normal attribute INSTANCES, so posing the mesh re-poses every layer.
  private mesh: THREE.Mesh | null = null;
  private lensMesh: THREE.Mesh | null = null;
  private lensOccludedMesh: THREE.Mesh | null = null;
  private depthPrepassMesh: THREE.Mesh | null = null;
  private selectionMesh: THREE.Mesh | null = null;
  private edgeLines: THREE.LineSegments | null = null; // BREP boundaries (persistent)
  private annotations = new THREE.Group(); // measurement etc. (persistent)
  private overlay = new THREE.Group(); // per-repaint overlays (cleared)
  private edgeUniforms = makeEdgeUniforms();
  private baseMaterial = makeBaseMaterial(this.edgeUniforms);
  private xrayMaterial = makeXrayMaterial();
  private lensMaterial = makeLensMaterial(this.edgeUniforms);
  private lensOccludedMaterial = makeLensOccludedMaterial();
  private selectionMaterial = makeSelectionMaterial(this.edgeUniforms);
  private depthPrepassMaterial = makeDepthPrepassMaterial();
  private viewport: ViewportState = DEFAULT_VIEWPORT;
  private theme: 'light' | 'dark' = 'dark';
  private lensHint = 1; // legacy setMeshOpacity: "this lens wants a see-through body"
  private findingMask: Uint8Array | null = null;
  private findingsVersion = 0;
  private lensAlphaState = ''; // skip redundant full-buffer alpha rewrites
  private selectionMask: Uint8Array | null = null;
  private selectionColorAttr: THREE.BufferAttribute | null = null;
  private posed = false; // vertex positions currently overridden (bend anim)
  // ONE shared section plane in a constant-length array assigned to every
  // material the scene creates (constant length → the clipping shader define
  // never changes, so no program relinks). Disabled = pushed to infinity.
  private clipPlane = new THREE.Plane(new THREE.Vector3(1, 0, 0), 1e9);
  private clipPlanes: THREE.Plane[] = [this.clipPlane];
  private colorAttr: THREE.BufferAttribute | null = null; // lens RGBA
  private faceCount = 0;
  private meshFaces: Uint32Array | null = null;
  private originalPositions: Float32Array | null = null;
  private originalNormals: Float32Array | null = null;
  private animator: ((tMs: number) => void) | null = null;
  private graphPoints: THREE.Points | null = null;
  private graphLines: THREE.LineSegments | null = null;
  private graphColorAttr: THREE.BufferAttribute | null = null;
  private graphNodeCount = 0;
  private graphKey = '';
  private raycaster = new THREE.Raycaster();
  private downAt: [number, number] | null = null;
  private disposed = false;
  // interactive axis gizmo (bottom-right): rotates with the camera, click an
  // axis to align the view. Assigned in the constructor.
  private viewHelper!: ViewHelper;
  private clock = new THREE.Clock();

  onPick: ((faceIndex: number, point: [number, number, number]) => void) | null = null;
  // arrow overlays are clickable: index into the direction set, or -1 for a
  // click that hit no arrow. The handler returns true when it consumed the
  // click (so the mesh pick underneath is skipped).
  private arrowHelpers: THREE.ArrowHelper[] = [];
  onPickArrow: ((index: number, screen: [number, number]) => boolean) | null = null;

  // camera handling lives in the rig (perspective/ortho switch, fits); the
  // rest of the class only ever needs "the active camera" and the controls
  private get camera() { return this.rig.camera; }
  private get controls() { return this.rig.controls; }

  constructor(private container: HTMLElement) {
    this.scene.background = new THREE.Color(0x21262c);
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setSize(container.clientWidth, container.clientHeight);
    // we clear manually each frame so the ViewHelper can overlay the gizmo
    this.renderer.autoClear = false;
    // per-material clipping (NOT renderer.clippingPlanes, which would also
    // cut the axis gizmo): every material the scene creates registers the
    // shared section plane
    this.renderer.localClippingEnabled = true;
    container.appendChild(this.renderer.domElement);
    for (const m of [this.baseMaterial, this.xrayMaterial, this.lensMaterial,
      this.lensOccludedMaterial, this.selectionMaterial,
      this.depthPrepassMaterial]) this.registerClipping(m);
    this.rig = new CameraRig(
      container.clientWidth / container.clientHeight,
      this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.15;
    this.controls.zoomToCursor = true; // wheel zooms toward the cursor
    this.controls.screenSpacePanning = true;
    // CAD muscle memory (NX / SolidWorks): middle-drag orbits, right-drag pans
    this.controls.mouseButtons = {
      LEFT: THREE.MOUSE.ROTATE,
      MIDDLE: THREE.MOUSE.ROTATE,
      RIGHT: THREE.MOUSE.PAN,
    };

    this.viewHelper = this.makeViewHelper();

    this.scene.add(this.overlay);
    this.scene.add(this.annotations);
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 1.0));
    const dir1 = new THREE.DirectionalLight(0xffffff, 0.55);
    dir1.position.set(-3, 10, -10);
    this.scene.add(dir1);
    const dir2 = new THREE.DirectionalLight(0xffffff, 0.35);
    dir2.position.set(3, -10, 10);
    this.scene.add(dir2);

    window.addEventListener('resize', this.onResize);
    this.renderer.domElement.addEventListener('pointerdown', this.onPointerDown);
    this.renderer.domElement.addEventListener('pointerup', this.onPointerUp);
    this.renderer.domElement.addEventListener('dblclick', this.onDoubleClick);

    const animate = () => {
      if (this.disposed) return;
      requestAnimationFrame(animate);
      const delta = this.clock.getDelta();
      if (this.animator) {
        try {
          this.animator(performance.now());
        } catch {
          this.animator = null; // a broken animator must not kill the loop
        }
      }
      // While a gizmo click is animating, ViewHelper drives the camera position
      // along the arc to the axis; controls.update() then re-derives the
      // orientation for our Z-up world, so there is no roll snap at the end.
      if (this.viewHelper.animating) this.viewHelper.update(delta);
      this.controls.update(); // damping needs a per-frame update
      this.renderer.clear();
      this.renderer.render(this.scene, this.camera);
      this.viewHelper.render(this.renderer);
    };
    animate();
  }

  /** Axis gizmo (bottom-right). `center` shares the controls' orbit target
   * (a live reference, so a double-click re-center moves the gizmo pivot
   * too). Rebuilt on a projection switch — the helper closure-captures its
   * camera. */
  private makeViewHelper(): ViewHelper {
    const helper = new ViewHelper(this.camera, this.renderer.domElement);
    helper.center = this.controls.target;
    helper.setLabelStyle('bold 22px system-ui, sans-serif', '#18181b', 15);
    helper.setLabels('X', 'Y', 'Z');
    return helper;
  }

  /** Switch perspective/orthographic, preserving target, orientation and
   * apparent model size (the rig owns the math). */
  setProjection(p: Projection) {
    if (!this.rig.setProjection(p)) return;
    this.viewHelper.dispose();
    this.viewHelper = this.makeViewHelper();
  }

  /** Fit the whole part in view, keeping the current view direction. */
  fit() {
    if (!this.mesh) return;
    const box = new THREE.Box3().setFromObject(this.mesh);
    const center = box.getCenter(new THREE.Vector3());
    const radius = box.getSize(new THREE.Vector3()).length() / 2;
    this.rig.fitTo(center, radius * 1.1);
  }

  private onResize = () => {
    const { clientWidth, clientHeight } = this.container;
    this.rig.onResize(clientWidth / clientHeight);
    this.renderer.setSize(clientWidth, clientHeight);
  };

  private onPointerDown = (e: PointerEvent) => {
    this.downAt = [e.clientX, e.clientY];
  };

  private hitUnderCursor(e: MouseEvent) {
    if (!this.mesh) return null;
    const rect = this.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(
      ((e.clientX - rect.left) / rect.width) * 2 - 1,
      -((e.clientY - rect.top) / rect.height) * 2 + 1,
    );
    this.raycaster.setFromCamera(ndc, this.camera);
    return this.raycaster.intersectObject(this.mesh)[0] ?? null;
  }

  private onPointerUp = (e: PointerEvent) => {
    // a drag is orbiting, not picking
    if (!this.downAt
      || Math.hypot(e.clientX - this.downAt[0], e.clientY - this.downAt[1]) > 4) return;
    // a click on a gizmo axis aligns the view; it consumes the click
    if (this.viewHelper.handleClick(e)) return;
    // arrows take precedence over the mesh (they float in front of the part),
    // but only the directions view consumes the click (see onPickArrow)
    const arrow = this.arrowUnderCursor(e);
    const consumed = this.onPickArrow ? this.onPickArrow(arrow, [e.clientX, e.clientY]) : false;
    if (consumed) return;
    const hit = this.hitUnderCursor(e);
    if (hit && hit.faceIndex != null && this.onPick) {
      this.onPick(hit.faceIndex, [hit.point.x, hit.point.y, hit.point.z]);
    }
  };

  /** Double-click re-centers the orbit pivot: on the clicked surface point
   * (rotate-about-point, like NX/SolidWorks), or back on the part's bounding
   * box center when clicking empty space. */
  private onDoubleClick = (e: MouseEvent) => {
    if (!this.mesh) return;
    const hit = this.hitUnderCursor(e);
    const target = hit
      ? hit.point.clone()
      : new THREE.Box3().setFromObject(this.mesh)
        .getCenter(new THREE.Vector3());
    this.controls.target.copy(target);
    this.controls.update();
  };

  /** Build the un-indexed geometry from raw vertex/index arrays.
   *  `faceNormals` are the per-face exact BREP surface normals (normals.npy);
   *  each face's normal is repeated for its three un-indexed vertices so
   *  curved faces shade with the true surface gradient instead of the
   *  tessellation's chord planes. Facet normals are the fallback. */
  setMesh(verts: Float32Array, faces: Uint32Array, faceNormals?: Float32Array) {
    this.clearMesh();
    this.faceCount = faces.length / 3;

    const positions = new Float32Array(faces.length * 3);
    for (let i = 0; i < faces.length; i++) {
      const v = faces[i];
      positions[3 * i] = verts[3 * v];
      positions[3 * i + 1] = verts[3 * v + 1];
      positions[3 * i + 2] = verts[3 * v + 2];
    }

    const positionAttr = new THREE.BufferAttribute(positions, 3);
    const baseGeometry = new THREE.BufferGeometry();
    baseGeometry.setAttribute('position', positionAttr);
    if (faceNormals && faceNormals.length === faces.length) {
      const normals = new Float32Array(faces.length * 3);
      for (let f = 0; f < this.faceCount; f++) {
        for (let corner = 0; corner < 3; corner++) {
          normals[9 * f + 3 * corner] = faceNormals[3 * f];
          normals[9 * f + 3 * corner + 1] = faceNormals[3 * f + 1];
          normals[9 * f + 3 * corner + 2] = faceNormals[3 * f + 2];
        }
      }
      baseGeometry.setAttribute('normal', new THREE.BufferAttribute(normals, 3));
    } else {
      baseGeometry.computeVertexNormals();
    }
    const normalAttr = baseGeometry.attributes.normal as THREE.BufferAttribute;
    const baryAttr = makeBaryAttribute(this.faceCount);
    baseGeometry.setAttribute('aBary', baryAttr);

    this.mesh = new THREE.Mesh(baseGeometry, this.baseMaterial);
    this.mesh.renderOrder = -1;

    // lens layer: shares position/normal/aBary, adds RGBA vertex colours
    // (itemSize 4 → three's USE_COLOR_ALPHA; the alpha channel implements
    // "findings only"). Painted grey/opaque so an unpainted mesh looks like
    // the classic viewer.
    const lensColors = new Float32Array(faces.length * 4);
    for (let i = 0; i < faces.length; i++) {
      lensColors[4 * i] = 0.9;
      lensColors[4 * i + 1] = 0.9;
      lensColors[4 * i + 2] = 0.9;
      lensColors[4 * i + 3] = 1;
    }
    const lensGeometry = new THREE.BufferGeometry();
    lensGeometry.setAttribute('position', positionAttr);
    lensGeometry.setAttribute('normal', normalAttr);
    lensGeometry.setAttribute('aBary', baryAttr);
    this.colorAttr = new THREE.BufferAttribute(lensColors, 4);
    lensGeometry.setAttribute('color', this.colorAttr);
    this.lensMesh = new THREE.Mesh(lensGeometry, this.lensMaterial);
    this.lensMesh.renderOrder = 1;
    this.lensOccludedMesh = new THREE.Mesh(lensGeometry, this.lensOccludedMaterial);
    this.lensOccludedMesh.renderOrder = 0;
    this.depthPrepassMesh = new THREE.Mesh(baseGeometry, this.depthPrepassMaterial);
    this.depthPrepassMesh.renderOrder = -2;
    // shared attributes leave the derived geometries' bounding spheres stale;
    // only the base mesh (the raycast target) keeps a live one
    for (const m of [this.lensMesh, this.lensOccludedMesh, this.depthPrepassMesh]) {
      m.frustumCulled = false;
      this.scene.add(m);
    }

    this.meshFaces = faces;
    this.originalPositions = positions.slice();
    this.originalNormals = (normalAttr.array as Float32Array).slice();
    this.posed = false;
    this.selectionMask = null;
    this.selectionColorAttr = null;
    this.findingMask = null;
    this.lensAlphaState = '';
    this.scene.add(this.mesh);
    this.applyRenderState();
  }

  /** Re-pose the mesh from indexed per-vertex positions (V*3), expanded
   * through the face index into the un-indexed buffer. null restores the
   * original mesh (including the exact BREP normals). `smooth` recomputes
   * lighting normals — skip it during playback and recompute on pause. */
  setVertexPositions(verts: Float32Array | null, smooth = true) {
    if (!this.mesh || !this.meshFaces) return;
    // position/normal attributes are SHARED across every render layer, so
    // writing the base geometry's arrays re-poses base, lens, prepass and
    // selection alike
    const geometry = this.mesh.geometry as THREE.BufferGeometry;
    const positionAttr = geometry.attributes.position as THREE.BufferAttribute;
    const positions = positionAttr.array as Float32Array;
    if (verts === null) {
      if (this.originalPositions) positions.set(this.originalPositions);
      if (this.originalNormals) {
        (geometry.attributes.normal.array as Float32Array)
          .set(this.originalNormals);
        (geometry.attributes.normal as THREE.BufferAttribute)
          .needsUpdate = true;
      }
    } else {
      const faces = this.meshFaces;
      for (let i = 0; i < faces.length; i++) {
        const v = faces[i];
        positions[3 * i] = verts[3 * v];
        positions[3 * i + 1] = verts[3 * v + 1];
        positions[3 * i + 2] = verts[3 * v + 2];
      }
      if (smooth) geometry.computeVertexNormals();
    }
    positionAttr.needsUpdate = true;
    geometry.computeBoundingSphere();
    // BREP boundary polylines are built from the ORIGINAL vertex positions —
    // hide them while posed rather than show them floating off the part
    this.posed = verts !== null;
    this.updateEdgeVisibility();
  }

  /** Surface normal at a face, read from the LIVE normal attribute: the
   * exact BREP normal when unposed, the recomputed geometric normal when
   * posed (stale during playing animation until pause recomputes). */
  faceNormalAt(f: number): [number, number, number] {
    if (!this.mesh) return [0, 0, 1];
    const normals = (this.mesh.geometry as THREE.BufferGeometry)
      .attributes.normal.array as Float32Array;
    return [normals[9 * f], normals[9 * f + 1], normals[9 * f + 2]];
  }

  /** Extrude a YZ profile polygon along machine X over the given spans and
   * add the pieces as (usually translucent) overlay meshes. */
  addOverlayMesh(spec: {
    profile: [number, number][];
    spans: [number, number][];
    color: RGB;
    opacity?: number;
    yzOffset?: [number, number];
    tag?: string;
  }) {
    const [dy, dz] = spec.yzOffset ?? [0, 0];
    const shape = new THREE.Shape();
    shape.moveTo(spec.profile[0][0] + dy, spec.profile[0][1] + dz);
    for (let i = 1; i < spec.profile.length; i++) {
      shape.lineTo(spec.profile[i][0] + dy, spec.profile[i][1] + dz);
    }
    shape.closePath();
    const opacity = spec.opacity ?? 1;
    for (const [x0, x1] of spec.spans) {
      const geometry = new THREE.ExtrudeGeometry(shape, {
        depth: x1 - x0, bevelEnabled: false,
      });
      const material = this.registerClipping(new THREE.MeshPhongMaterial({
        color: new THREE.Color(...spec.color),
        transparent: opacity < 1,
        opacity,
        depthWrite: opacity >= 1,
        side: THREE.DoubleSide,
      }));
      const mesh = new THREE.Mesh(geometry, material);
      // shape (u,v) is machine (Y,Z); the extrusion axis is machine X
      mesh.matrixAutoUpdate = false;
      mesh.matrix.set(
        0, 0, 1, x0,
        1, 0, 0, 0,
        0, 1, 0, 0,
        0, 0, 0, 1,
      );
      if (spec.tag) mesh.userData.tag = spec.tag;
      mesh.renderOrder = 2; // after the lens layer in the transparent pass
      this.overlay.add(mesh);
    }
  }

  /** Move every overlay mesh carrying `tag` to world height dz (absolute,
   * not cumulative) — e.g. the punch/ram following the stroke. */
  shiftOverlay(tag: string, dz: number) {
    for (const child of this.overlay.children) {
      if (child.userData.tag === tag) {
        // Matrix4.elements is column-major: [14] is the z translation
        child.matrix.elements[14] = dz;
      }
    }
  }

  /** Register a per-frame callback run inside the render loop (null to
   * remove). Only one animator at a time; the controller resets it on
   * every repaint. */
  setAnimator(fn: ((tMs: number) => void) | null) {
    this.animator = fn;
  }

  /** Look at the part from an approach direction, slightly tilted. */
  frame(direction: number[] | null) {
    if (!this.mesh) return;
    const box = new THREE.Box3().setFromObject(this.mesh);
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3()).length();
    this.rig.setWorldRadius(size / 2);
    const d = direction ?? [0, 0, 1];
    const view = new THREE.Vector3(d[0], d[1], d[2]).normalize();
    const side = new THREE.Vector3(0, 1, 0);
    if (Math.abs(view.dot(side)) > 0.9) side.set(1, 0, 0);
    side.cross(view).normalize();
    const position = center.clone()
      .addScaledVector(view, size * 0.9)
      .addScaledVector(side, size * 0.45);
    this.rig.lookFrom(position, center,
      this.rig.halfHeightFor(position.distanceTo(center)));
  }

  paintFaces(colorOf: (f: number) => RGB) {
    if (!this.colorAttr) return;
    for (let f = 0; f < this.faceCount; f++) {
      const [r, g, b] = colorOf(f);
      for (let k = 0; k < 3; k++) this.colorAttr.setXYZ(3 * f + k, r, g, b);
    }
    this.colorAttr.needsUpdate = true;
  }

  /** Per-corner colors: the GPU interpolates them across each face, so a
   * per-vertex scalar field renders as a smooth gradient. */
  paintCorners(colorOf: (f: number, k: number) => RGB) {
    if (!this.colorAttr) return;
    for (let f = 0; f < this.faceCount; f++) {
      for (let k = 0; k < 3; k++) {
        const [r, g, b] = colorOf(f, k);
        this.colorAttr.setXYZ(3 * f + k, r, g, b);
      }
    }
    this.colorAttr.needsUpdate = true;
  }

  /** Fly the camera to look at a region from along `direction`, at a
   * distance suiting the region (but never so close the part vanishes). */
  flyTo(center: [number, number, number], direction: [number, number, number],
        radius: number) {
    if (!this.mesh) return;
    const c = new THREE.Vector3(...center);
    const dir = new THREE.Vector3(...direction);
    if (dir.lengthSq() < 1e-9) {
      dir.copy(this.camera.position).sub(this.controls.target);
      if (dir.lengthSq() < 1e-9) dir.set(0, 0, 1);
    }
    dir.normalize();
    const partSize = new THREE.Box3().setFromObject(this.mesh)
      .getSize(new THREE.Vector3()).length();
    const dist = Math.max(radius * 3, partSize * 0.12);
    this.rig.lookFrom(c.clone().addScaledVector(dir, dist), c,
      this.rig.halfHeightFor(dist));
  }

  /** Overlay line segments given as flattened endpoint pairs (N*2*3).
   * depthTest true keeps them on the visible surface (e.g. isolines);
   * false (default) draws them through the part (e.g. parting lines). */
  setLines(positions: Float32Array, color: RGB = [1.0, 0.85, 0.2],
           depthTest = false) {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    // transparent (even at opacity 1) so the lines sort into the transparent
    // pass AFTER the lens layer — an opaque depth-less line would be painted
    // over by the lens overlay
    const material = this.registerClipping(new THREE.LineBasicMaterial({
      color: new THREE.Color(...color), depthTest, depthWrite: false,
      transparent: true,
    }));
    const lines = new THREE.LineSegments(geometry, material);
    lines.renderOrder = depthTest ? 2 : 4; // through-lines draw over the mesh
    this.overlay.add(lines);
  }

  /** Overlay one arrow per direction, pointing at the part from outside.
   * Arrows are click-pickable (see onPickArrow) — each carries its index. */
  setArrows(arrows: { direction: number[]; color: RGB }[]) {
    if (!this.mesh) return;
    this.arrowHelpers = [];
    const box = new THREE.Box3().setFromObject(this.mesh);
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3()).length();
    arrows.forEach(({ direction, color }, index) => {
      const d = new THREE.Vector3(
        direction[0], direction[1], direction[2]).normalize();
      const length = 0.22 * size;
      const origin = center.clone().addScaledVector(d, 0.55 * size + length);
      const helper = new THREE.ArrowHelper(
        d.clone().negate(), origin, length,
        new THREE.Color(...color).getHex(), 0.3 * length, 0.12 * length);
      this.registerClipping(helper.line.material as THREE.Material);
      this.registerClipping(helper.cone.material as THREE.Material);
      helper.userData.arrowIndex = index;
      this.overlay.add(helper);
      this.arrowHelpers.push(helper);
    });
  }

  /** Index of the arrow under the cursor, or -1. Cones are the hit target. */
  private arrowUnderCursor(e: MouseEvent): number {
    if (!this.arrowHelpers.length) return -1;
    const rect = this.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(
      ((e.clientX - rect.left) / rect.width) * 2 - 1,
      -((e.clientY - rect.top) / rect.height) * 2 + 1,
    );
    this.raycaster.setFromCamera(ndc, this.camera);
    const hits = this.raycaster.intersectObjects(this.arrowHelpers, true);
    for (const hit of hits) {
      let o: THREE.Object3D | null = hit.object;
      while (o && o.userData.arrowIndex === undefined) o = o.parent;
      if (o) return o.userData.arrowIndex as number;
    }
    return -1;
  }

  clearOverlays() {
    this.arrowHelpers = [];
    for (const child of [...this.overlay.children]) {
      this.overlay.remove(child);
      if (child instanceof THREE.LineSegments || child instanceof THREE.Mesh) {
        child.geometry.dispose();
        (child.material as THREE.Material).dispose();
      } else if (child instanceof THREE.ArrowHelper) {
        child.line.geometry.dispose();
        (child.line.material as THREE.Material).dispose();
        child.cone.geometry.dispose();
        (child.cone.material as THREE.Material).dispose();
      }
    }
  }

  /**
   * Show a graph overlay: nodes as radius-sized points, edges as line
   * segments sharing the node position/color buffers (two draw calls, and
   * edge colors interpolate the endpoint node colors for free). Rendered
   * without depth so the skeleton stays visible inside the part. A repeat
   * call with the same key keeps the buffers and only awaits paintGraph.
   */
  setGraph(key: string, nodes: Float32Array, edges: Uint32Array, radii: Float32Array) {
    if (key === this.graphKey) return;
    this.clearGraph();
    this.graphKey = key;
    this.graphNodeCount = nodes.length / 3;

    const positionAttr = new THREE.BufferAttribute(nodes, 3);
    const colorAttr = new THREE.BufferAttribute(
      new Float32Array(nodes.length).fill(0.9), 3);
    const sizeAttr = new THREE.BufferAttribute(radii, 1);

    const pointGeometry = new THREE.BufferGeometry();
    pointGeometry.setAttribute('position', positionAttr);
    pointGeometry.setAttribute('color', colorAttr);
    pointGeometry.setAttribute('size', sizeAttr);
    // hand-written shader: the clipping chunks must be included explicitly
    // (and `clipping: true` set) for the section plane to apply
    const pointMaterial = this.registerClipping(new THREE.ShaderMaterial({
      depthTest: false,
      depthWrite: false,
      transparent: true,
      clipping: true,
      vertexShader: `
        #include <common>
        #include <clipping_planes_pars_vertex>
        attribute float size;
        varying vec3 vColor;
        void main() {
          vColor = color;
          vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
          gl_PointSize = clamp(300.0 * size / -mvPosition.z, 2.0, 24.0);
          gl_Position = projectionMatrix * mvPosition;
          #include <clipping_planes_vertex>
        }`,
      fragmentShader: `
        #include <common>
        #include <clipping_planes_pars_fragment>
        varying vec3 vColor;
        void main() {
          #include <clipping_planes_fragment>
          if (length(gl_PointCoord - 0.5) > 0.5) discard;
          gl_FragColor = vec4(vColor, 1.0);
        }`,
      vertexColors: true,
    }));
    this.graphPoints = new THREE.Points(pointGeometry, pointMaterial);
    this.graphPoints.renderOrder = 4; // after the lens layer (transparent pass)

    const lineGeometry = new THREE.BufferGeometry();
    lineGeometry.setAttribute('position', positionAttr);
    lineGeometry.setAttribute('color', colorAttr);
    lineGeometry.setIndex(new THREE.BufferAttribute(edges, 1));
    const lineMaterial = this.registerClipping(new THREE.LineBasicMaterial({
      vertexColors: true, transparent: true, opacity: 0.8,
      depthTest: false, depthWrite: false,
    }));
    this.graphLines = new THREE.LineSegments(lineGeometry, lineMaterial);
    this.graphLines.renderOrder = 3; // after the lens layer (transparent pass)

    this.graphColorAttr = colorAttr;
    this.scene.add(this.graphLines);
    this.scene.add(this.graphPoints);
  }

  paintGraph(colorOf: (node: number) => RGB) {
    if (!this.graphColorAttr) return;
    for (let n = 0; n < this.graphNodeCount; n++) {
      const [r, g, b] = colorOf(n);
      this.graphColorAttr.setXYZ(n, r, g, b);
    }
    this.graphColorAttr.needsUpdate = true;
  }

  clearGraph() {
    for (const object of [this.graphPoints, this.graphLines]) {
      if (!object) continue;
      this.scene.remove(object);
      object.geometry.dispose();
      (object.material as THREE.Material).dispose();
    }
    this.graphPoints = null;
    this.graphLines = null;
    this.graphColorAttr = null;
    this.graphNodeCount = 0;
    this.graphKey = '';
  }

  /** Set the scene clear color (viewer background) and restyle the neutral
   * layers (base grey, edge colours, X-ray shell) for light/dark mode. */
  setBackground(color: string | number, theme: 'light' | 'dark' = 'dark') {
    this.scene.background = new THREE.Color(color as THREE.ColorRepresentation);
    this.theme = theme;
    const colors = THEME_COLORS[theme];
    this.baseMaterial.color.setHex(colors.base);
    this.edgeUniforms.uEdgeColor.value.setHex(colors.triEdge);
    (this.xrayMaterial.uniforms.uBase.value as THREE.Color).setHex(colors.xrayBase);
    (this.xrayMaterial.uniforms.uRim.value as THREE.Color).setHex(colors.xrayRim);
    if (this.edgeLines) {
      (this.edgeLines.material as THREE.LineBasicMaterial)
        .color.setHex(colors.brepEdge);
    }
  }

  /** One frame rendered and read back as PNG, plus the full camera pose —
   * report evidence. Rendering immediately before toDataURL makes the
   * readback valid without preserveDrawingBuffer. The pose is a superset of
   * the original {position, target} shape so existing consumers keep
   * working. */
  capture(): { image: string;
    camera: { position: number[]; target: number[]; up: number[];
      projection: Projection; fov?: number; zoom?: number } } {
    this.renderer.render(this.scene, this.camera);
    const camera = this.camera;
    return {
      image: this.renderer.domElement.toDataURL('image/png'),
      camera: {
        position: camera.position.toArray(),
        target: this.controls.target.toArray(),
        up: camera.up.toArray(),
        projection: this.rig.projection,
        ...(camera instanceof THREE.PerspectiveCamera
          ? { fov: camera.fov } : { zoom: camera.zoom }),
      },
    };
  }

  /** Legacy `ViewCtx.setMeshOpacity`, demoted to a transient per-paint hint:
   * "this lens wants a see-through body" (skeleton/graph/flat-pattern
   * views). Reset to 1 by the controller on every repaint; composed with
   * the persistent viewport state in applyRenderState. */
  setLensDisplayHint(alpha: number) {
    if (this.lensHint === alpha) return;
    this.lensHint = alpha;
    this.applyRenderState();
  }

  /** Persistent viewport state changed (render style, edges, projection,
   * section, context). Applied directly — no repaint needed, the lens data
   * is unchanged. */
  setViewport(vs: ViewportState) {
    this.viewport = vs;
    this.setProjection(vs.projection);
    this.applySection();
    this.applyRenderState();
  }

  /** Register a material for the shared section plane. */
  private registerClipping<T extends THREE.Material>(material: T): T {
    material.clippingPlanes = this.clipPlanes;
    return material;
  }

  /** Aim the shared clipping plane per the viewport's section state: cut
   * away the half-space the (possibly flipped) normal points into, past
   * `offset` along the ORIGINAL normal. Disabled = plane at infinity. */
  private applySection() {
    const s = this.viewport.section;
    if (!s.enabled) {
      this.clipPlane.constant = 1e9;
      return;
    }
    const sign = s.flip ? -1 : 1;
    const n = new THREE.Vector3(...s.normal).normalize()
      .multiplyScalar(sign);
    // three clips fragments with negative signed distance: keep
    // dot(N, p) <= dot(N, p0) where p0 = normal * offset
    this.clipPlane.normal.copy(n).negate();
    this.clipPlane.constant = sign * s.offset;
  }

  /** Part bounding box (posed), for the section offset range. */
  getBounds(): { min: [number, number, number]; max: [number, number, number] } | null {
    if (!this.mesh) return null;
    const box = new THREE.Box3().setFromObject(this.mesh);
    if (box.isEmpty()) return null;
    return {
      min: [box.min.x, box.min.y, box.min.z],
      max: [box.max.x, box.max.y, box.max.z],
    };
  }

  /** Unit direction the camera looks along (target − position). */
  getViewDirection(): [number, number, number] {
    const dir = this.controls.target.clone()
      .sub(this.camera.position);
    if (dir.lengthSq() < 1e-12) return [0, 0, 1];
    dir.normalize();
    return [dir.x, dir.y, dir.z];
  }

  /** Which faces the active lens counts as findings (null = no notion).
   * Reset by the controller on every repaint; drives "findings only". */
  setFindings(isFinding: ((f: number) => boolean) | null) {
    if (!isFinding) {
      this.findingMask = null;
      return;
    }
    const mask = new Uint8Array(this.faceCount);
    for (let f = 0; f < this.faceCount; f++) mask[f] = isFinding(f) ? 1 : 0;
    this.findingMask = mask;
    this.findingsVersion++; // a new mask invalidates the written alpha state
  }

  /** The legend-group selection (fine-face indices), rendered by the
   * selection layer under ghost/isolate and targeted by fitSelection. */
  setSelectionFaces(faces: number[] | null) {
    if (!faces || !this.faceCount) {
      this.selectionMask = null;
    } else {
      const mask = new Uint8Array(this.faceCount);
      for (const f of faces) if (f < this.faceCount) mask[f] = 1;
      this.selectionMask = mask;
    }
    this.applyRenderState();
  }

  /** Fit the current selection in view (posed corner positions). */
  fitSelection() {
    if (!this.mesh || !this.selectionMask) return;
    const positions = (this.mesh.geometry as THREE.BufferGeometry)
      .attributes.position.array as Float32Array;
    const box = new THREE.Box3();
    const v = new THREE.Vector3();
    for (let f = 0; f < this.faceCount; f++) {
      if (!this.selectionMask[f]) continue;
      for (let k = 0; k < 3; k++) {
        const i = 3 * (3 * f + k);
        box.expandByPoint(v.set(positions[i], positions[i + 1], positions[i + 2]));
      }
    }
    if (box.isEmpty()) return;
    const center = box.getCenter(new THREE.Vector3());
    const radius = box.getSize(new THREE.Vector3()).length() / 2;
    this.rig.fitTo(center, Math.max(radius * 1.2, 1e-3));
  }

  /** Persistent BREP boundary polylines (segment endpoints, N*2*3 floats);
   * null removes them. Survives repaints — only a part switch clears it. */
  setBrepEdges(segments: Float32Array | null) {
    if (this.edgeLines) {
      this.scene.remove(this.edgeLines);
      this.edgeLines.geometry.dispose();
      (this.edgeLines.material as THREE.Material).dispose();
      this.edgeLines = null;
    }
    if (!segments || !segments.length) return;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(segments, 3));
    const material = this.registerClipping(new THREE.LineBasicMaterial({
      color: new THREE.Color(THEME_COLORS[this.theme].brepEdge),
    }));
    this.edgeLines = new THREE.LineSegments(geometry, material);
    this.edgeLines.renderOrder = 2;
    this.scene.add(this.edgeLines);
    this.updateEdgeVisibility();
  }

  private updateEdgeVisibility() {
    if (this.edgeLines) {
      this.edgeLines.visible = this.viewport.edgeMode === 'brep' && !this.posed
        && this.viewport.context !== 'isolate';
    }
  }

  /** Rewrite the lens alpha channel for the findings-only filter (skipped
   * when the desired state already applies — it touches every corner). */
  private refreshLensAlpha() {
    if (!this.colorAttr) return;
    const mask = this.viewport.findingsOnly ? this.findingMask : null;
    const state = mask ? `mask:${this.findingsVersion}` : 'all';
    if (state === this.lensAlphaState) return;
    this.lensAlphaState = state;
    for (let f = 0; f < this.faceCount; f++) {
      const alpha = mask && !mask[f] ? 0 : 1;
      for (let k = 0; k < 3; k++) this.colorAttr.setW(3 * f + k, alpha);
    }
    this.colorAttr.needsUpdate = true;
  }

  /** (Re)build the selection layer's colours: the lens RGB where selected
   * (so ghost/isolate show the real lens data), discarded elsewhere. */
  private refreshSelectionColors() {
    if (!this.mesh || !this.lensMesh || !this.selectionMask || !this.colorAttr) return;
    if (!this.selectionMesh) {
      const geometry = new THREE.BufferGeometry();
      const baseGeometry = this.mesh.geometry as THREE.BufferGeometry;
      geometry.setAttribute('position', baseGeometry.attributes.position);
      geometry.setAttribute('normal', baseGeometry.attributes.normal);
      geometry.setAttribute('aBary', baseGeometry.attributes.aBary);
      this.selectionColorAttr = new THREE.BufferAttribute(
        new Float32Array(this.faceCount * 12), 4);
      geometry.setAttribute('color', this.selectionColorAttr);
      this.selectionMesh = new THREE.Mesh(geometry, this.selectionMaterial);
      this.selectionMesh.renderOrder = 0;
      this.selectionMesh.frustumCulled = false;
      this.scene.add(this.selectionMesh);
    }
    const lens = this.colorAttr;
    const sel = this.selectionColorAttr!;
    for (let f = 0; f < this.faceCount; f++) {
      const selected = this.selectionMask[f];
      for (let k = 0; k < 3; k++) {
        const i = 3 * f + k;
        sel.setXYZW(i, lens.getX(i), lens.getY(i), lens.getZ(i), selected ? 1 : 0);
      }
    }
    sel.needsUpdate = true;
  }

  /** Compose the persistent viewport state with the transient lens display
   * hint into concrete material/visibility settings on every layer. Called
   * after each repaint and on every viewport change. */
  applyRenderState() {
    if (!this.mesh || !this.lensMesh || !this.lensOccludedMesh
      || !this.depthPrepassMesh) return;
    const vs = this.viewport;
    const xray = vs.style === 'xray';
    const facets = vs.style === 'facets';
    const hasSelection = !!this.selectionMask;
    const ghost = vs.context === 'ghost' && hasSelection;
    const isolate = vs.context === 'isolate' && hasSelection;

    // base: opaque phong for shaded/facets, the Fresnel shell for X-ray
    this.mesh.material = xray ? this.xrayMaterial : this.baseMaterial;
    this.mesh.visible = !isolate;
    this.depthPrepassMesh.visible = xray && !isolate;

    // flat shading (facets) on every phong layer; triangle edges are part of
    // the facets look and additive via the tessellation edge mode
    for (const m of [this.baseMaterial, this.lensMaterial,
      this.lensOccludedMaterial, this.selectionMaterial]) {
      if (m.flatShading !== facets) {
        m.flatShading = facets;
        m.needsUpdate = true;
      }
    }
    this.edgeUniforms.uEdges.value =
      facets || vs.edgeMode === 'tessellation' ? 1 : 0;
    this.updateEdgeVisibility();

    // base opacity: the lens display hint × ghosting (X-ray is already
    // see-through and ignores the hint)
    const baseAlpha = Math.min(this.lensHint, ghost ? GHOST_ALPHA : 1);
    if (!xray) {
      this.baseMaterial.transparent = baseAlpha < 1;
      this.baseMaterial.opacity = baseAlpha;
      this.baseMaterial.depthWrite = baseAlpha >= 1;
      this.baseMaterial.needsUpdate = true;
    }

    // lens overlay: visibility, opacity (three multiplies material opacity
    // with the per-vertex alpha), findings-only alpha, X-ray occluded pass
    const lensAlpha = vs.lensOpacity
      * Math.min(this.lensHint, ghost ? GHOST_ALPHA : 1);
    this.lensMesh.visible = vs.lensVisible && !isolate && lensAlpha > 0;
    this.lensMaterial.opacity = lensAlpha;
    this.lensMaterial.depthWrite = false;
    this.lensOccludedMesh.visible = xray && this.lensMesh.visible;
    this.lensOccludedMaterial.opacity = lensAlpha * 0.25;
    this.refreshLensAlpha();

    // selection layer: only meaningful under ghost/isolate
    if (ghost || isolate) this.refreshSelectionColors();
    if (this.selectionMesh) {
      this.selectionMesh.visible = (ghost || isolate) && hasSelection;
    }
  }

  clearMesh() {
    this.clearOverlays();
    this.clearGraph();
    this.clearAnnotations();
    this.setBrepEdges(null);
    this.animator = null;
    // dispose every layer geometry; the shared attributes go with them (the
    // materials are long-lived and reused by the next part)
    for (const m of [this.mesh, this.lensMesh, this.lensOccludedMesh,
      this.depthPrepassMesh, this.selectionMesh]) {
      if (!m) continue;
      this.scene.remove(m);
    }
    this.mesh?.geometry.dispose();
    this.lensMesh?.geometry.dispose(); // shared by lensOccludedMesh
    this.selectionMesh?.geometry.dispose();
    this.mesh = null;
    this.lensMesh = null;
    this.lensOccludedMesh = null;
    this.depthPrepassMesh = null;
    this.selectionMesh = null;
    this.selectionColorAttr = null;
    this.selectionMask = null;
    this.findingMask = null;
    this.lensAlphaState = '';
    this.posed = false;
    this.colorAttr = null;
    this.faceCount = 0;
    this.meshFaces = null;
    this.originalPositions = null;
    this.originalNormals = null;
  }

  /** Rebuild the measurement annotations from the session state: A/B
   * markers (constant screen size), the straight A→B segment, RGB
   * model-axis component legs anchored at A, and the two normal rays.
   * Lives in the persistent annotation layer — lens repaints never clear
   * it — and is registered for section clipping like every other layer. */
  setMeasureAnnotations(a: MeasurePick | null, b: MeasurePick | null) {
    this.clearAnnotations();
    if (!a && !b) return;
    const bounds = this.getBounds();
    const diag = bounds
      ? Math.hypot(bounds.max[0] - bounds.min[0], bounds.max[1] - bounds.min[1],
                   bounds.max[2] - bounds.min[2])
      : 10;
    const rayLength = diag * 0.05;

    const addLine = (points: [number, number, number][], color: number,
                     opacity = 1) => {
      const positions = new Float32Array(points.flat());
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      const material = this.registerClipping(new THREE.LineBasicMaterial({
        color, transparent: true, opacity, depthTest: false, depthWrite: false,
      }));
      const line = new THREE.Line(geometry, material);
      line.renderOrder = 5;
      this.annotations.add(line);
    };
    const addMarker = (pick: MeasurePick, label: string, color: string) => {
      const canvas = document.createElement('canvas');
      canvas.width = 64;
      canvas.height = 64;
      const g = canvas.getContext('2d')!;
      g.beginPath();
      g.arc(32, 32, 26, 0, 2 * Math.PI);
      g.fillStyle = color;
      g.fill();
      g.lineWidth = 5;
      g.strokeStyle = '#ffffff';
      g.stroke();
      g.fillStyle = '#ffffff';
      g.font = 'bold 30px system-ui, sans-serif';
      g.textAlign = 'center';
      g.textBaseline = 'middle';
      g.fillText(label, 32, 34);
      const material = this.registerClipping(new THREE.SpriteMaterial({
        map: new THREE.CanvasTexture(canvas),
        depthTest: false, sizeAttenuation: false, // constant screen size
      }));
      const sprite = new THREE.Sprite(material);
      sprite.position.set(...pick.point);
      sprite.scale.set(0.045, 0.045, 1);
      sprite.renderOrder = 6;
      this.annotations.add(sprite);
      // the pick's surface normal, drawn from the picked point
      const tip: [number, number, number] = [
        pick.point[0] + pick.normal[0] * rayLength,
        pick.point[1] + pick.normal[1] * rayLength,
        pick.point[2] + pick.normal[2] * rayLength,
      ];
      addLine([pick.point, tip], 0xbfc6ce, 0.9);
    };

    if (a) addMarker(a, 'A', '#2f6fde');
    if (b) addMarker(b, 'B', '#d97b16');
    if (a && b) {
      addLine([a.point, b.point], this.theme === 'dark' ? 0xf3f5f7 : 0x1c1f24);
      // RGB axis staircase A → +dX → +dY → +dZ = B
      const p1: [number, number, number] = [b.point[0], a.point[1], a.point[2]];
      const p2: [number, number, number] = [b.point[0], b.point[1], a.point[2]];
      addLine([a.point, p1], 0xe14b4b, 0.9);
      addLine([p1, p2], 0x3fba55, 0.9);
      addLine([p2, b.point], 0x4b86e1, 0.9);
    }
  }

  /** Remove everything in the persistent annotation layer (measurement
   * markers). Called on part switch and by the measurement session. */
  clearAnnotations() {
    for (const child of [...this.annotations.children]) {
      this.annotations.remove(child);
      const obj = child as THREE.Mesh | THREE.Line | THREE.Sprite;
      if ('geometry' in obj) (obj.geometry as THREE.BufferGeometry)?.dispose();
      const material = (obj as { material?: THREE.Material }).material;
      if (material) {
        const map = (material as THREE.SpriteMaterial).map;
        map?.dispose();
        material.dispose();
      }
    }
  }

  dispose() {
    this.disposed = true;
    this.clearMesh();
    for (const m of [this.baseMaterial, this.xrayMaterial, this.lensMaterial,
      this.lensOccludedMaterial, this.selectionMaterial,
      this.depthPrepassMaterial]) m.dispose();
    this.viewHelper.dispose();
    window.removeEventListener('resize', this.onResize);
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }
}
