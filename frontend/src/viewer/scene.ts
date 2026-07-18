// Plain three.js scene: one un-indexed mesh with per-vertex colors written
// imperatively (three vertices per face, so faceIndex * 3 addresses a face's
// corners — the same contract the old viewer relied on).

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import type { RGB } from '../registry/types';

export class Scene3D {
  private scene = new THREE.Scene();
  private camera: THREE.PerspectiveCamera;
  private renderer: THREE.WebGLRenderer;
  private controls: OrbitControls;
  private mesh: THREE.Mesh | null = null;
  private overlay = new THREE.Group();
  private colorAttr: THREE.BufferAttribute | null = null;
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

  onPick: ((faceIndex: number, point: [number, number, number]) => void) | null = null;

  constructor(private container: HTMLElement) {
    this.scene.background = new THREE.Color(0x21262c);
    this.camera = new THREE.PerspectiveCamera(
      50, container.clientWidth / container.clientHeight, 0.1, 5000);
    // Z is up for CAD parts — must be set BEFORE constructing OrbitControls,
    // which captures its orbit basis from camera.up at construction time
    // (setting it later leaves the controls tumbling around +Y)
    this.camera.up.set(0, 0, 1);
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setSize(container.clientWidth, container.clientHeight);
    container.appendChild(this.renderer.domElement);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
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

    this.scene.add(this.overlay);
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
      if (this.animator) {
        try {
          this.animator(performance.now());
        } catch {
          this.animator = null; // a broken animator must not kill the loop
        }
      }
      this.controls.update(); // damping needs a per-frame update
      this.renderer.render(this.scene, this.camera);
    };
    animate();
  }

  private onResize = () => {
    const { clientWidth, clientHeight } = this.container;
    this.camera.aspect = clientWidth / clientHeight;
    this.camera.updateProjectionMatrix();
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

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('color',
      new THREE.BufferAttribute(new Float32Array(faces.length * 3).fill(0.9), 3));
    if (faceNormals && faceNormals.length === faces.length) {
      const normals = new Float32Array(faces.length * 3);
      for (let f = 0; f < this.faceCount; f++) {
        for (let corner = 0; corner < 3; corner++) {
          normals[9 * f + 3 * corner] = faceNormals[3 * f];
          normals[9 * f + 3 * corner + 1] = faceNormals[3 * f + 1];
          normals[9 * f + 3 * corner + 2] = faceNormals[3 * f + 2];
        }
      }
      geometry.setAttribute('normal', new THREE.BufferAttribute(normals, 3));
    } else {
      geometry.computeVertexNormals();
    }

    const material = new THREE.MeshPhongMaterial({
      vertexColors: true, specular: 0x111111, shininess: 18,
    });
    this.mesh = new THREE.Mesh(geometry, material);
    this.colorAttr = geometry.attributes.color as THREE.BufferAttribute;
    this.meshFaces = faces;
    this.originalPositions = positions.slice();
    this.originalNormals = (geometry.attributes.normal.array as Float32Array)
      .slice();
    this.scene.add(this.mesh);
  }

  /** Re-pose the mesh from indexed per-vertex positions (V*3), expanded
   * through the face index into the un-indexed buffer. null restores the
   * original mesh (including the exact BREP normals). `smooth` recomputes
   * lighting normals — skip it during playback and recompute on pause. */
  setVertexPositions(verts: Float32Array | null, smooth = true) {
    if (!this.mesh || !this.meshFaces) return;
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
      const material = new THREE.MeshPhongMaterial({
        color: new THREE.Color(...spec.color),
        transparent: opacity < 1,
        opacity,
        depthWrite: opacity >= 1,
        side: THREE.DoubleSide,
      });
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
      mesh.renderOrder = 1;
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
    const d = direction ?? [0, 0, 1];
    const view = new THREE.Vector3(d[0], d[1], d[2]).normalize();
    const side = new THREE.Vector3(0, 1, 0);
    if (Math.abs(view.dot(side)) > 0.9) side.set(1, 0, 0);
    side.cross(view).normalize();
    this.camera.position.copy(center)
      .addScaledVector(view, size * 0.9)
      .addScaledVector(side, size * 0.45);
    this.camera.up.set(0, 0, 1);
    this.controls.target.copy(center);
    this.controls.update();
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
    this.camera.position.copy(c).addScaledVector(dir, dist);
    this.controls.target.copy(c);
    this.controls.update();
  }

  /** Overlay line segments given as flattened endpoint pairs (N*2*3).
   * depthTest true keeps them on the visible surface (e.g. isolines);
   * false (default) draws them through the part (e.g. parting lines). */
  setLines(positions: Float32Array, color: RGB = [1.0, 0.85, 0.2],
           depthTest = false) {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    const material = new THREE.LineBasicMaterial({
      color: new THREE.Color(...color), depthTest, depthWrite: false,
    });
    const lines = new THREE.LineSegments(geometry, material);
    lines.renderOrder = depthTest ? 0 : 1; // through-lines draw over the mesh
    this.overlay.add(lines);
  }

  /** Overlay one arrow per direction, pointing at the part from outside. */
  setArrows(arrows: { direction: number[]; color: RGB }[]) {
    if (!this.mesh) return;
    const box = new THREE.Box3().setFromObject(this.mesh);
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3()).length();
    for (const { direction, color } of arrows) {
      const d = new THREE.Vector3(
        direction[0], direction[1], direction[2]).normalize();
      const length = 0.22 * size;
      const origin = center.clone().addScaledVector(d, 0.55 * size + length);
      this.overlay.add(new THREE.ArrowHelper(
        d.clone().negate(), origin, length,
        new THREE.Color(...color).getHex(), 0.3 * length, 0.12 * length));
    }
  }

  clearOverlays() {
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
    const pointMaterial = new THREE.ShaderMaterial({
      depthTest: false,
      depthWrite: false,
      transparent: true,
      vertexShader: `
        attribute float size;
        varying vec3 vColor;
        void main() {
          vColor = color;
          vec4 mv = modelViewMatrix * vec4(position, 1.0);
          gl_PointSize = clamp(300.0 * size / -mv.z, 2.0, 24.0);
          gl_Position = projectionMatrix * mv;
        }`,
      fragmentShader: `
        varying vec3 vColor;
        void main() {
          if (length(gl_PointCoord - 0.5) > 0.5) discard;
          gl_FragColor = vec4(vColor, 1.0);
        }`,
      vertexColors: true,
    });
    this.graphPoints = new THREE.Points(pointGeometry, pointMaterial);
    this.graphPoints.renderOrder = 2;

    const lineGeometry = new THREE.BufferGeometry();
    lineGeometry.setAttribute('position', positionAttr);
    lineGeometry.setAttribute('color', colorAttr);
    lineGeometry.setIndex(new THREE.BufferAttribute(edges, 1));
    const lineMaterial = new THREE.LineBasicMaterial({
      vertexColors: true, transparent: true, opacity: 0.8,
      depthTest: false, depthWrite: false,
    });
    this.graphLines = new THREE.LineSegments(lineGeometry, lineMaterial);
    this.graphLines.renderOrder = 1;

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

  setMeshOpacity(alpha: number) {
    if (!this.mesh) return;
    const material = this.mesh.material as THREE.MeshPhongMaterial;
    material.transparent = alpha < 1;
    material.opacity = alpha;
    material.depthWrite = alpha >= 1;
    material.needsUpdate = true;
  }

  clearMesh() {
    this.clearOverlays();
    this.clearGraph();
    this.animator = null;
    if (this.mesh) {
      this.scene.remove(this.mesh);
      this.mesh.geometry.dispose();
      (this.mesh.material as THREE.Material).dispose();
      this.mesh = null;
      this.colorAttr = null;
      this.faceCount = 0;
      this.meshFaces = null;
      this.originalPositions = null;
      this.originalNormals = null;
    }
  }

  dispose() {
    this.disposed = true;
    this.clearMesh();
    window.removeEventListener('resize', this.onResize);
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }
}
