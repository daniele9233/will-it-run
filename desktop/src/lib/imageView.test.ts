import { describe, expect, it } from "vitest";

import { FIT, MAX_ZOOM, MIN_ZOOM, actualSizeZoom, clampView, clampZoom, fitSize, zoomAt, zoomPercent } from "./imageView";

const frame = { width: 800, height: 600 };
const photo = { width: 1024, height: 1536 };

describe("fitting a picture in a frame", () => {
  it("makes it as large as the frame allows and never larger than itself", () => {
    const shown = fitSize(photo, frame);
    expect(shown.height).toBe(600);
    expect(shown.width).toBeCloseTo(400, 5);
    expect(fitSize({ width: 100, height: 50 }, frame)).toEqual({ width: 100, height: 50 });
  });

  it("says nothing while the picture or the frame is not known yet", () => {
    expect(fitSize({ width: 0, height: 0 }, frame)).toEqual({ width: 0, height: 0 });
    expect(fitSize(photo, { width: 0, height: 0 })).toEqual({ width: 0, height: 0 });
  });
});

describe("zoom", () => {
  it("stays between fit and the maximum, whatever it is asked", () => {
    expect(clampZoom(0.2)).toBe(MIN_ZOOM);
    expect(clampZoom(99)).toBe(MAX_ZOOM);
    expect(clampZoom(Number.NaN)).toBe(MIN_ZOOM);
  });

  it("reads 100% when one picture pixel is one screen pixel", () => {
    const shown = fitSize(photo, frame);
    const actual = actualSizeZoom(photo, shown);
    expect(actual).toBeCloseTo(2.56, 2);
    expect(zoomPercent(photo, shown, actual)).toBe(100);
    expect(zoomPercent(photo, shown, 1)).toBe(39);
  });

  it("does not go below fit when the picture is smaller than the frame", () => {
    const small = { width: 200, height: 100 };
    expect(actualSizeZoom(small, fitSize(small, frame))).toBe(1);
  });
});

describe("moving a zoomed picture", () => {
  const shown = fitSize(photo, frame);

  it("keeps a picture that fits in the middle", () => {
    expect(clampView({ zoom: 1, x: 300, y: -200 }, frame, shown)).toEqual(FIT);
  });

  it("lets a zoomed picture move only until its edge reaches the frame's", () => {
    const view = clampView({ zoom: 4, x: 9999, y: -9999 }, frame, shown);
    expect(view.x).toBeCloseTo((shown.width * 4 - frame.width) / 2, 5);
    expect(view.y).toBeCloseTo(-(shown.height * 4 - frame.height) / 2, 5);
  });

  it("keeps the point under the pointer where it was when zooming in", () => {
    const start = { zoom: 2, x: 40, y: -30 };
    const pointer = { x: 120, y: 50 };
    const after = zoomAt(start, 1.5, pointer, frame, shown);
    const local = (view: typeof start) => ({ x: (pointer.x - view.x) / view.zoom, y: (pointer.y - view.y) / view.zoom });
    expect(after.zoom).toBeCloseTo(3, 5);
    expect(local(after).x).toBeCloseTo(local(start).x, 4);
    expect(local(after).y).toBeCloseTo(local(start).y, 4);
  });

  it("returns to the middle when zooming all the way out", () => {
    expect(zoomAt({ zoom: 3, x: 200, y: 100 }, 0.01, { x: 0, y: 0 }, frame, shown)).toEqual(FIT);
  });
});
