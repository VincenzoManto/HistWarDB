// Scroll-reveal for elements marked with .reveal
const revealEls = document.querySelectorAll(".reveal");
if ("IntersectionObserver" in window && revealEls.length) {
  const io = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("in");
          io.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.15, rootMargin: "0px 0px -60px 0px" }
  );
  revealEls.forEach((el) => io.observe(el));
} else {
  revealEls.forEach((el) => el.classList.add("in"));
}

// Animated stat counters
const counters = document.querySelectorAll("[data-count]");
counters.forEach((el) => {
  const target = parseFloat(el.dataset.count);
  const suffix = el.dataset.suffix || "";
  const decimals = el.dataset.count.includes(".") ? 1 : 0;
  let started = false;

  const run = () => {
    if (started) return;
    started = true;
    const duration = 1400;
    const start = performance.now();
    const step = (now) => {
      const p = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - p, 3);
      el.textContent = (target * eased).toFixed(decimals) + suffix;
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  };

  if ("IntersectionObserver" in window) {
    const obs = new IntersectionObserver(
      (entries) => entries.forEach((e) => e.isIntersecting && run()),
      { threshold: 0.4 }
    );
    obs.observe(el);
  } else {
    run();
  }
});

// Copy-to-clipboard for citation block
document.querySelectorAll("[data-copy]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const target = document.querySelector(btn.getAttribute("data-copy"));
    if (!target) return;
    try {
      await navigator.clipboard.writeText(target.textContent.trim());
      const original = btn.textContent;
      btn.textContent = "Copied!";
      setTimeout(() => (btn.textContent = original), 1600);
    } catch (err) {
      /* clipboard unavailable: no-op */
    }
  });
});

// Subtle parallax on hero blobs following the pointer (disabled on touch)
const blobs = document.querySelectorAll(".fx-blobs span");
if (window.matchMedia("(pointer: fine)").matches && blobs.length) {
  window.addEventListener("pointermove", (e) => {
    const x = (e.clientX / window.innerWidth - 0.5) * 2;
    const y = (e.clientY / window.innerHeight - 0.5) * 2;
    blobs.forEach((b, i) => {
      const depth = (i + 1) * 10;
      b.style.transform = `translate(${x * depth}px, ${y * depth}px)`;
    });
  });
}
