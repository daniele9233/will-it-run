// A horizontal strip moved by the wheel; the data file is fetched only on the first wheel event.
let x = 0;
let fetched = false;
window.addEventListener("wheel", (e) => {
  x = Math.max(0, x + e.deltaY);
  document.getElementById("strip").style.transform = `translateX(${-x}px)`;
  if (!fetched) { fetched = true; fetch("wheel-data.json").catch(() => {}); }
}, { passive: true });
