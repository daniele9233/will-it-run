// Fetches a video by range at load and loads late.js only after the first scroll event.
fetch("clip.mp4", { headers: { Range: "bytes=0-99" } }).catch(() => {});
window.addEventListener("scroll", () => {
  const s = document.createElement("script");
  s.src = "late.js";
  document.body.appendChild(s);
}, { once: true });
