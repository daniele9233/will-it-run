// Zooming and moving a picture inside a frame, as plain arithmetic so it can be tested.
//
// The picture is drawn at its "fit" size (zoom 1: as large as the frame allows, never larger than itself) and scaled about
// the centre of the frame. `x` and `y` say how far its centre has been moved from the centre of the frame, in screen pixels.

import { clamp } from "./format";

export interface View {
  zoom: number;
  x: number;
  y: number;
}

export interface Size {
  width: number;
  height: number;
}

export const MIN_ZOOM = 1;
export const MAX_ZOOM = 12;
export const STEP = 1.25;
export const FIT: View = { zoom: 1, x: 0, y: 0 };

export function clampZoom(zoom: number): number {
  return Number.isFinite(zoom) ? clamp(zoom, MIN_ZOOM, MAX_ZOOM) : MIN_ZOOM;
}

/** The size of the picture at zoom 1 inside the frame. Zero while either is not known yet. */
export function fitSize(natural: Size, frame: Size): Size {
  if (!natural.width || !natural.height || !frame.width || !frame.height) return { width: 0, height: 0 };
  const scale = Math.min(1, frame.width / natural.width, frame.height / natural.height);
  return { width: natural.width * scale, height: natural.height * scale };
}

/** The picture can be moved only as far as its edge reaches the edge of the frame; smaller than the frame, it stays centred. */
export function clampView(view: View, frame: Size, shown: Size): View {
  const zoom = clampZoom(view.zoom);
  const limitX = Math.max(0, (shown.width * zoom - frame.width) / 2);
  const limitY = Math.max(0, (shown.height * zoom - frame.height) / 2);
  // "+ 0" turns a negative zero into a zero: a picture centred by a limit of nothing is at 0, not at -0.
  return { zoom, x: clamp(view.x, -limitX, limitX) + 0, y: clamp(view.y, -limitY, limitY) + 0 };
}

/** Zoom by `factor` and keep the point under the pointer where it is. `point` is measured from the centre of the frame. */
export function zoomAt(view: View, factor: number, point: { x: number; y: number }, frame: Size, shown: Size): View {
  const zoom = clampZoom(view.zoom * factor);
  const ratio = zoom / view.zoom;
  return clampView({ zoom, x: point.x - (point.x - view.x) * ratio, y: point.y - (point.y - view.y) * ratio }, frame, shown);
}

/** The zoom at which one pixel of the picture is one pixel of the screen. */
export function actualSizeZoom(natural: Size, shown: Size): number {
  if (!natural.width || !shown.width) return 1;
  return clampZoom(natural.width / shown.width);
}

/** The zoom as the person reads it: 100% is one picture pixel per screen pixel. */
export function zoomPercent(natural: Size, shown: Size, zoom: number): number {
  if (!natural.width || !shown.width) return Math.round(zoom * 100);
  return Math.round(((shown.width * zoom) / natural.width) * 100);
}
