import { gsap } from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { Viewer, SceneFormat } from '@mkkellogg/gaussian-splats-3d';

gsap.registerPlugin(ScrollTrigger);
const $ = id => document.getElementById(id);
const canvas = $('canvas'), ctx = canvas.getContext('2d');
let frames = [], frame = 0, generation = 0, animation, drag;
let drawing = 0;
const decoded = new Map();
function releaseFrames() {
  frames.forEach(url => URL.revokeObjectURL(url));
  frames = []; decoded.clear(); drawing++;
}
const playhead = { frame: 0 };
async function draw(value) {
  if (!frames.length) return;
  const token = ++drawing;
  frame = ((Math.round(value) % frames.length) + frames.length) % frames.length;
  let image = decoded.get(frame);
  if (!image) {
    image = new Image(); image.src = frames[frame];
    try {await image.decode();} catch {if (token === drawing) $('status').textContent = `Cannot decode frame ${frame + 1}`; return;}
    if (token !== drawing) return;
    decoded.set(frame, image);
    while (decoded.size > 8) decoded.delete(decoded.keys().next().value);
  }
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const scale = Math.min(canvas.width / image.width, canvas.height / image.height);
  ctx.drawImage(image, (canvas.width - image.width * scale) / 2,
    (canvas.height - image.height * scale) / 2, image.width * scale, image.height * scale);
  $('seek').value = frame;
  $('overlay').textContent = `${frame + 1} / ${frames.length} · ${['Initial state', 'Transition — inspect motion', 'Final state'][Math.min(2, Math.floor(frame / frames.length * 3))]}`;
}
function configure() {
  animation?.scrollTrigger?.kill();
  animation?.kill();
  draw(0);
  if ($('mode').value === 'scroll' && frames.length && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
    playhead.frame = 0;
    animation = gsap.to(playhead, {frame: frames.length - 1, ease: 'none',
      onUpdate: () => draw(playhead.frame),
      scrollTrigger: {trigger: '#stage', start: 'top top', end: '+=2400', pin: true, scrub: 0.3}});
    ScrollTrigger.refresh();
  }
}
$('load').onclick = async () => {
  const token = ++generation;
  $('status').textContent = 'Loading frames…';
  try {
    const url = new URL($('path').value, location.href);
    if (url.origin !== location.origin) throw Error('Use a local manifest from this server.');
    const response = await fetch(url);
    if (!response.ok) throw Error(`Manifest HTTP ${response.status}`);
    const manifest = await response.json();
    if (!Array.isArray(manifest.frames) || !manifest.frames.length || manifest.frames.length > 300) throw Error('Expected 1–300 frames.');
    // Browser cache holds compressed images; bounded workers avoid 300 simultaneous downloads.
    const loaded = new Array(manifest.frames.length);
    let next = 0;
    const results = await Promise.allSettled(Array.from({length: 6}, async () => {
      while (next < loaded.length && token === generation) {
        const i = next++;
        const src = new URL(manifest.frames[i], url);
        if (src.origin !== location.origin) throw Error('External frames are not allowed.');
        const response = await fetch(src);
        if (!response.ok) throw Error(`Frame ${i + 1}: HTTP ${response.status}`);
        loaded[i] = URL.createObjectURL(await response.blob());
      }
    }));
    const failed = results.find(result => result.status === 'rejected');
    if (failed) {loaded.forEach(url => URL.revokeObjectURL(url)); throw failed.reason;}
    if (token !== generation) {loaded.forEach(url => URL.revokeObjectURL(url)); return;}
    releaseFrames();
    frames = loaded;
    $('seek').disabled = false; $('seek').max = frames.length - 1;
    $('mode').value = manifest.scenario === 'spin' ? 'spin' : 'scroll';
    configure();
    $('status').textContent = `${frames.length} frames loaded. Scroll, drag in spin mode, or use the frame slider.`;
    const report = await fetch(new URL('report.json', url));
    if (token === generation) $('report').textContent = report.ok ? JSON.stringify(await report.json(), null, 2) : 'Report unavailable.';
  } catch (error) { if (token === generation) $('status').textContent = `Load failed: ${error.message}`; }
};
$('seek').oninput = event => draw(Number(event.target.value));
$('mode').onchange = configure;
canvas.onkeydown = event => {
  if (['ArrowLeft', 'ArrowRight'].includes(event.key)) {event.preventDefault(); draw(frame + (event.key === 'ArrowRight' ? 1 : -1));}
};
canvas.onpointerdown = event => {
  if ($('mode').value !== 'spin' || !frames.length) return;
  drag = {x: event.clientX, frame}; canvas.setPointerCapture(event.pointerId);
};
canvas.onpointermove = event => {if (drag) draw(drag.frame + (event.clientX - drag.x) * frames.length / canvas.clientWidth);};
canvas.onpointerup = canvas.onpointercancel = canvas.onlostpointercapture = () => { drag = null; };
let viewer, splatGeneration = 0;
$('file').onchange = async event => {
  const file = event.target.files[0]; if (!file) return;
  if (file.size > 200 * 1024 * 1024) { $('splat-status').textContent = 'Fixture exceeds the 200 MB inspection budget.'; return; }
  const token = ++splatGeneration;
  $('file').disabled = true;
  const url = URL.createObjectURL(file);
  try {
    await viewer?.dispose();
    viewer = new Viewer({rootElement: $('splat'), sharedMemoryForWorkers: false,
      cameraUp: [0, 1, 0], initialCameraPosition: [0, 0, 5], initialCameraLookAt: [0, 0, 0]});
    $('splat-status').textContent = 'Loading Gaussian scene…';
    await viewer.addSplatScene(url, {format: file.name.toLowerCase().endsWith('.ply') ? SceneFormat.Ply : SceneFormat.Splat,
      showLoadingUI: true});
    if (token === splatGeneration) {viewer.start(); $('splat-status').textContent = 'Scene ready. Drag to orbit.';}
  } catch (error) { $('splat-status').textContent = `Scene failed: ${error.message}`; }
  finally {URL.revokeObjectURL(url); $('file').disabled = false;}
};
window.addEventListener('pagehide', () => {generation++; animation?.scrollTrigger?.kill(); animation?.kill(); void viewer?.dispose(); releaseFrames();});
async function refreshSuite() {
  try {
    const response = await fetch('/runs/summary.json', {cache: 'no-store'});
    if (!response.ok) return;
    const summary = await response.json();
    $('suite').textContent = `Suite: ${summary.status || 'running'} · ${summary.cases.length} recorded cases${summary.error ? ' · ' + summary.error : ''}`;
    for (const entry of summary.cases) {
      if (!['passed_screening', 'failed'].includes(entry.status)) continue;
      const path = `/runs/${entry.scenario}-${entry.model}-${entry.repeat}/manifest.json`;
      if (Array.from($('cases').options).some(option => option.value === path)) continue;
      const available = await fetch(path);
      if (!available.ok || !available.headers.get('content-type')?.includes('json')) continue;
      const option = new Option(`${entry.scenario} · ${entry.model} · ${entry.status}`, path);
      $('cases').add(option);
      if (!frames.length) { $('path').value = path; await $('load').onclick(); $('cases').value = path; }
    }
  } catch { $('suite').textContent = 'Waiting for a readable local suite report.'; }
}
$('cases').onchange = () => {if ($('cases').value) {$('path').value = $('cases').value; void $('load').onclick();}};
void refreshSuite();
const refreshTimer = setInterval(refreshSuite, 15000);
window.addEventListener('pagehide', () => clearInterval(refreshTimer));
