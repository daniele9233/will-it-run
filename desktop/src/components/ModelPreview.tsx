import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { DRACOLoader } from "three/addons/loaders/DRACOLoader.js";
import dracoWrapper from "three/examples/jsm/libs/draco/gltf/draco_wasm_wrapper.js?url";
import dracoWasm from "three/examples/jsm/libs/draco/gltf/draco_decoder.wasm?url";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { t } from "../lib/i18n";
import { errorMessage } from "../lib/hooks";
import { Button } from "./ui";

function release(root: THREE.Object3D) {
  const textures = new Set<THREE.Texture>();
  root.traverse((object) => {
    if (!(object instanceof THREE.Mesh)) return;
    object.geometry.dispose();
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
      for (const value of Object.values(material)) if (value instanceof THREE.Texture) textures.add(value);
      material.dispose();
    }
  });
  textures.forEach((texture) => { texture.dispose(); if (texture.image instanceof ImageBitmap) texture.image.close(); });
}

function triangles(root: THREE.Object3D): number {
  let count = 0;
  root.traverse((object) => {
    if (object instanceof THREE.Mesh) count += (object.geometry.index?.count ?? object.geometry.getAttribute("position")?.count ?? 0) / 3;
  });
  return Math.round(count);
}

/** One large studio viewport with local assets and explicit GPU resource cleanup. */
export function ModelPreview({ data, comparison, clay = false, height, onSnapshot }: { data: ArrayBuffer; comparison?: ArrayBuffer; clay?: boolean; height: number; onSnapshot?: (image: string) => void }) {
  const host = useRef<HTMLDivElement>(null);
  const originalView = useRef<HTMLDivElement>(null);
  const comparisonView = useRef<HTMLDivElement>(null);
  const reset = useRef(() => {});
  const move = useRef((_angle: number, _zoom: number) => {});
  const [error, setError] = useState("");
  const [counts, setCounts] = useState<{ original: number | null; web: number | null }>({ original: null, web: null });
  useEffect(() => {
    const element = host.current!;
    const areas = comparison ? [originalView.current!, comparisonView.current!] : [originalView.current!];
    let disposed = false;
    let renderer: THREE.WebGLRenderer;
    queueMicrotask(() => { if (!disposed) { setError(""); setCounts({ original: null, web: null }); } });
    try { renderer = new THREE.WebGLRenderer({ antialias: true }); }
    catch (err) { queueMicrotask(() => { if (!disposed) { setError(errorMessage(err)); onSnapshot?.(""); } }); return () => { disposed = true; }; }
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.domElement.style.cssText = "position:absolute;inset:0;width:100%;height:100%;pointer-events:none";
    element.prepend(renderer.domElement);
    const room = new RoomEnvironment();
    const pmrem = new THREE.PMREMGenerator(renderer);
    const environment = pmrem.fromScene(room);
    room.dispose(); pmrem.dispose();
    const scenes = areas.map(() => {
      const scene = new THREE.Scene();
      scene.background = new THREE.Color(0x141419);
      scene.environment = environment.texture;
      scene.environmentIntensity = 0.4;
      scene.add(new THREE.HemisphereLight(0xffffff, 0x505060, 0.8));
      const key = new THREE.DirectionalLight(0xffffff, 1.6); key.position.set(3, 4, 5); scene.add(key);
      return scene;
    });
    const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 100);
    const controls = areas.map((area) => new OrbitControls(camera, area));
    let radius = 1;
    let fittedDistance = 1;
    let ready = false;
    const render = () => {
      if (disposed || !element.clientWidth || !element.clientHeight) return;
      renderer.setSize(element.clientWidth, element.clientHeight, false);
      const bounds = element.getBoundingClientRect();
      renderer.setScissorTest(false);
      renderer.setClearColor(0x141419); renderer.clear();
      renderer.setScissorTest(true);
      areas.forEach((area, i) => {
        if (!ready) return;
        const rect = area.getBoundingClientRect();
        if (!rect.width || !rect.height) return;
        renderer.setViewport(rect.left - bounds.left, bounds.bottom - rect.bottom, rect.width, rect.height);
        renderer.setScissor(rect.left - bounds.left, bounds.bottom - rect.bottom, rect.width, rect.height);
        camera.aspect = rect.width / rect.height; camera.updateProjectionMatrix();
        renderer.render(scenes[i]!, camera);
      });
    };
    const distanceToFit = () => {
      const area = areas[0]!;
      const aspect = Math.max(area.clientWidth, 1) / Math.max(area.clientHeight, 1);
      const fov = THREE.MathUtils.degToRad(camera.fov / 2);
      return radius / Math.sin(Math.min(fov, Math.atan(Math.tan(fov) * aspect))) * 1.12;
    };
    const fit = () => {
      fittedDistance = distanceToFit();
      camera.position.set(0, 0, fittedDistance);
      controls.forEach((control) => control.target.set(0, 0, 0));
      controls[0]!.update(); render();
    };
    reset.current = fit;
    move.current = (angle, zoom) => {
      const target = controls[0]!.target;
      const spherical = new THREE.Spherical().setFromVector3(camera.position.clone().sub(target));
      spherical.theta += angle; spherical.radius *= zoom;
      camera.position.copy(target).add(new THREE.Vector3().setFromSpherical(spherical));
      controls.forEach((control) => control.target.copy(target));
      controls[0]!.update(); render();
    };
    controls.forEach((control) => {
      control.addEventListener("change", () => {
        controls.forEach((other) => { if (other !== control) other.target.copy(control.target); });
        render();
      });
    });
    const observer = new ResizeObserver(() => {
      if (!element.clientWidth || !element.clientHeight) return;
      renderer.setSize(element.clientWidth, element.clientHeight, false);
      if (ready) {
        const target = controls[0]!.target;
        const offset = camera.position.clone().sub(target);
        const nextDistance = distanceToFit();
        offset.multiplyScalar(nextDistance / fittedDistance);
        camera.position.copy(target).add(offset);
        fittedDistance = nextDistance;
        controls[0]!.update();
      }
      render();
    });
    observer.observe(element); areas.forEach((area) => observer.observe(area));
    const manager = new THREE.LoadingManager();
    manager.setURLModifier((url) => {
      if (url === "wir-draco/draco_wasm_wrapper.js") return dracoWrapper;
      if (url === "wir-draco/draco_decoder.wasm") return dracoWasm;
      if (!url.startsWith("blob:") && !url.startsWith("data:")) throw new Error(t("The model contains external resources."));
      return url;
    });
    manager.onError = () => { if (!disposed) setError(t("The model texture could not be loaded.")); };
    const decoder = new DRACOLoader(manager).setDecoderPath({ js: "wir-draco/draco_wasm_wrapper.js", wasm: "wir-draco/draco_decoder.wasm" });
    const loader = new GLTFLoader(manager).setDRACOLoader(decoder);
    void Promise.allSettled((comparison ? [data, comparison] : [data]).map(bytes => loader.parseAsync(bytes, ""))).then((results) => {
      const failed = results.find(result => result.status === "rejected");
      if (disposed || failed) {
        results.forEach(result => { if (result.status === "fulfilled") release(result.value.scene); });
        if (failed) throw failed.reason;
        return;
      }
      let fittedRadius = 0.01;
      results.forEach((result, index) => {
        if (result.status !== "fulfilled") return;
        const original = result.value;
        const box = new THREE.Box3().setFromObject(original.scene);
        const center = box.getCenter(new THREE.Vector3());
        const size = box.getSize(new THREE.Vector3());
        const group = new THREE.Group();
        original.scene.position.sub(center); group.add(original.scene);
        if (comparison) group.scale.setScalar(1 / Math.max(size.x, size.y, size.z, 0.001));
        if (comparison || clay) {
          const textures = new Set<THREE.Texture>();
          original.scene.traverse(object => {
            if (!(object instanceof THREE.Mesh)) return;
            for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
              for (const value of Object.values(material)) if (value instanceof THREE.Texture) textures.add(value);
              material.dispose();
            }
            object.material = new THREE.MeshStandardMaterial({ color: 0xa9afb8, roughness: 0.78, metalness: 0 });
            object.geometry.computeVertexNormals();
          });
          textures.forEach(texture => { texture.dispose(); if (texture.image instanceof ImageBitmap) texture.image.close(); });
        }
        scenes[index]!.add(group);
        fittedRadius = Math.max(fittedRadius, size.length() / 2 * group.scale.x);
        setCounts(value => ({ ...value, [index === 0 ? "original" : "web"]: triangles(original.scene) }));
      });
      radius = fittedRadius;
      camera.near = radius / 100; camera.far = radius * 100;
      ready = true; fit();
      if (onSnapshot) { render(); onSnapshot(renderer.domElement.toDataURL("image/webp", 0.8)); }
    }).catch((err) => { if (!disposed) { setError(errorMessage(err)); onSnapshot?.(""); } });
    return () => {
      disposed = true; observer.disconnect(); controls.forEach((control) => control.dispose()); decoder.dispose();
      scenes.forEach(release); environment.dispose(); renderer.dispose(); renderer.forceContextLoss(); renderer.domElement.remove();
      reset.current = () => {}; move.current = () => {};
    };
  }, [data, comparison, clay, onSnapshot]);
  return <div className="grid min-h-0 gap-3">
    <div ref={host} className="relative overflow-hidden rounded-lg bg-[#141419]" style={{ height }}>
      <div className={`relative z-1 grid h-full ${comparison ? "grid-cols-2" : ""}`}>
        {[{ ref: originalView, title: comparison ? t("Source model") : t("Model preview"), count: counts.original, bytes: data.byteLength, available: true }, ...(comparison ? [{ ref: comparisonView, title: "Hi3D", count: counts.web, bytes: comparison.byteLength, available: true }] : [])].map((view) => <div key={view.title} className="flex min-h-0 min-w-0 flex-col">
          {!onSnapshot && <div className="grid shrink-0 gap-1 bg-panel px-3 py-2 text-xs"><strong className="font-semibold">{view.title}</strong><span className="text-ink-3">{view.count !== null ? `${view.count.toLocaleString()} ${t("triangles")}` : view.available ? t("Loading…") : t("Not generated yet")}{view.bytes !== undefined && ` · ${(view.bytes / 1048576).toFixed(2)} MB`}</span></div>}
          <div ref={view.ref} className="relative min-h-0 flex-1 touch-none" role="img" aria-label={view.title}>
            {!view.available && <div className="absolute inset-0 grid place-items-center bg-panel p-4 text-center text-sm text-ink-3">{t("Create a Web copy to compare it with the original.")}</div>}
          </div>
        </div>)}
      </div>
    </div>
    {error && <p role="alert" className="text-sm text-bad">{error}</p>}
    {!onSnapshot && <div className="flex flex-wrap items-center gap-2">
      <Button size="sm" onClick={() => reset.current()}>{t("Reset view")}</Button>
      <Button size="sm" onClick={() => move.current(-Math.PI / 8, 1)}>{t("Rotate left")}</Button>
      <Button size="sm" onClick={() => move.current(Math.PI / 8, 1)}>{t("Rotate right")}</Button>
      <Button size="sm" onClick={() => move.current(0, 0.8)}>{t("Zoom in")}</Button>
      <Button size="sm" onClick={() => move.current(0, 1.25)}>{t("Zoom out")}</Button>
      <span className="text-xs text-ink-3">{t("Drag to rotate. Scroll to zoom.")}</span>
    </div>}
  </div>;
}
