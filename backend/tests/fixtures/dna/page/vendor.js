// A stand-in for a vendor bundle: it only has to hold the call a real ScrollTrigger page makes.
window.addEventListener("load", function () {
  if (window.ScrollTrigger && window.ScrollTrigger.create) {
    ScrollTrigger.create({ trigger: "body" });
  }
});
