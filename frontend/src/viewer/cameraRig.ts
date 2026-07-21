// Both viewer cameras plus the shared OrbitControls: the rig owns the
// perspective/orthographic switch so camera handling stays behind Scene3D.
// A switch preserves the orbit target, orientation and apparent model size:
// the ortho frustum height matches the perspective view cone at the target
// distance, and vice versa.

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import type { Projection } from './viewportState';

const FOV = 50;
const TAN_HALF_FOV = Math.tan(THREE.MathUtils.degToRad(FOV / 2));

export class CameraRig {
  readonly persp: THREE.PerspectiveCamera;
  readonly ortho: THREE.OrthographicCamera;
  readonly controls: OrbitControls;
  private active: Projection = 'perspective';
  /** Scene bounding radius — sizes the ortho frustum and its clip range. */
  private radius = 1;
  private aspect: number;

  constructor(aspect: number, domElement: HTMLElement) {
    this.aspect = aspect;
    this.persp = new THREE.PerspectiveCamera(FOV, aspect, 0.1, 5000);
    this.ortho = new THREE.OrthographicCamera(-aspect, aspect, 1, -1, -50, 100);
    // Z is up for CAD parts — must be set on BOTH cameras BEFORE constructing
    // OrbitControls, which captures its orbit basis from camera.up at
    // construction time (setting it later leaves the controls tumbling
    // around +Y)
    this.persp.up.set(0, 0, 1);
    this.ortho.up.set(0, 0, 1);
    // a default framing so the empty viewer is a real 3D view before a part
    // loads; frame() overrides it
    this.persp.position.set(4, -4, 3);
    this.controls = new OrbitControls(this.persp, domElement);
  }

  get camera(): THREE.PerspectiveCamera | THREE.OrthographicCamera {
    return this.active === 'perspective' ? this.persp : this.ortho;
  }

  get projection(): Projection {
    return this.active;
  }

  /** View half-height at distance d from a perspective camera. */
  halfHeightFor(distance: number): number {
    return distance * TAN_HALF_FOV;
  }

  /** Half-height of the current view at the orbit target — the "apparent
   * model size" a projection switch must preserve. */
  private apparentHalfHeight(): number {
    if (this.active === 'perspective') {
      return this.halfHeightFor(
        this.persp.position.distanceTo(this.controls.target));
    }
    return this.ortho.top / this.ortho.zoom;
  }

  /** The part changed (or loaded): size the ortho frustum to the scene. */
  setWorldRadius(r: number) {
    this.radius = Math.max(r, 1e-6);
    this.syncOrthoFrustum();
  }

  private syncOrthoFrustum() {
    const r = this.radius;
    this.ortho.top = r;
    this.ortho.bottom = -r;
    this.ortho.left = -r * this.aspect;
    this.ortho.right = r * this.aspect;
    // negative near is legal for ortho: geometry behind the camera position
    // still renders, so orbiting close never clips the part
    const d = this.ortho.position.distanceTo(this.controls.target);
    this.ortho.near = -4 * r;
    this.ortho.far = d + 8 * r;
    this.ortho.updateProjectionMatrix();
  }

  /** Switch projection, preserving target, orientation and apparent size.
   * Returns true when the projection actually changed. */
  setProjection(p: Projection): boolean {
    if (p === this.active) return false;
    const h = this.apparentHalfHeight();
    const target = this.controls.target;
    if (p === 'orthographic') {
      this.ortho.position.copy(this.persp.position);
      this.ortho.quaternion.copy(this.persp.quaternion);
      this.syncOrthoFrustum();
      this.ortho.zoom = this.ortho.top / h;
      this.ortho.updateProjectionMatrix();
    } else {
      const dir = target.clone().sub(this.ortho.position);
      if (dir.lengthSq() < 1e-12) dir.set(0, 0, -1);
      else dir.normalize();
      this.persp.position.copy(target)
        .addScaledVector(dir, -(h / TAN_HALF_FOV));
      this.persp.quaternion.copy(this.ortho.quaternion);
    }
    this.active = p;
    this.controls.object = this.camera;
    this.controls.update();
    return true;
  }

  onResize(aspect: number) {
    this.aspect = aspect;
    this.persp.aspect = aspect;
    this.persp.updateProjectionMatrix();
    this.syncOrthoFrustum(); // keeps zoom, rescales left/right
  }

  /** Place the active camera at `position` looking at `target`, with
   * `halfHeight` as the apparent size (ortho reads it into zoom; for
   * perspective it is implied by the distance the caller chose). */
  lookFrom(position: THREE.Vector3, target: THREE.Vector3,
           halfHeight: number) {
    this.camera.position.copy(position);
    this.controls.target.copy(target);
    if (this.active === 'orthographic') {
      this.ortho.zoom = this.ortho.top / halfHeight;
      this.ortho.updateProjectionMatrix();
    }
    this.controls.update();
  }

  /** Re-aim at `center` with apparent half-height `h`, keeping the current
   * view direction (fit part / fit selection). */
  fitTo(center: THREE.Vector3, h: number) {
    const dir = this.camera.position.clone().sub(this.controls.target);
    if (dir.lengthSq() < 1e-12) dir.set(4, -4, 3);
    dir.normalize();
    const distance = this.active === 'perspective'
      ? h / TAN_HALF_FOV
      : this.ortho.position.distanceTo(this.controls.target) || 4 * this.radius;
    this.lookFrom(
      center.clone().addScaledVector(dir, distance), center, h);
  }
}
