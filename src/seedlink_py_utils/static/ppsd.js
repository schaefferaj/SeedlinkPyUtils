// Swap in a PPSD PNG when ppsd-archive rewrites it. Polls each image's
// mtime rather than re-fetching the PNG. Only visible images are checked
// (not in a hidden view or a collapsed <details>), so a station page with
// hundreds of buckets stays cheap.
(function () {
  const MTIME_POLL_MS = 30000;

  async function checkOneImage(img) {
    const relpath = img.dataset.relpath;
    const oldMtime = parseInt(img.dataset.mtime, 10);
    try {
      const resp = await fetch(`/ppsd/file/${relpath}/mtime`);
      if (!resp.ok) return;
      const data = await resp.json();
      if (data.mtime && data.mtime !== oldMtime) {
        img.dataset.mtime = String(data.mtime);
        const base = img.src.split('?')[0];
        img.src = base + '?cb=' + data.mtime;
      }
    } catch (e) {
      // Silent — network blip, next tick will retry.
    }
  }

  async function checkAllImages() {
    const imgs = Array.from(document.querySelectorAll('.ppsd-image'))
      .filter(img => img.offsetParent !== null
                     && !img.closest('details:not([open])'));
    await Promise.all(imgs.map(checkOneImage));
  }

  setInterval(checkAllImages, MTIME_POLL_MS);
})();
