let cachedLogoPromise: Promise<HTMLImageElement | null> | null = null;

/**
 * Loads the official transparent Fikstra logo mark (/logo.png) for canvas PNG cards.
 */
export function loadOfficialLogoMark(): Promise<HTMLImageElement | null> {
  if (cachedLogoPromise) return cachedLogoPromise;
  cachedLogoPromise = new Promise((resolve) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = "/logo.png";
  });
  return cachedLogoPromise;
}

export function drawOfficialLogoMark(
  ctx: CanvasRenderingContext2D,
  logoImg: HTMLImageElement | null,
  x: number,
  y: number,
  size: number,
) {
  ctx.save();
  if (logoImg) {
    ctx.drawImage(logoImg, x, y, size, size);
  } else {
    // Fallback geometric F mark matching /logo.svg
    const s = size / 512;
    const fg = ctx.createLinearGradient(x + 120 * s, y + 96 * s, x + 392 * s, y + 416 * s);
    fg.addColorStop(0, "#34D399");
    fg.addColorStop(0.55, "#10B981");
    fg.addColorStop(1, "#06B6D4");
    ctx.fillStyle = fg;
    ctx.beginPath();
    ctx.moveTo(x + 154 * s, y + 118 * s);
    ctx.lineTo(x + 372 * s, y + 118 * s);
    ctx.lineTo(x + 346 * s, y + 184 * s);
    ctx.lineTo(x + 224 * s, y + 184 * s);
    ctx.lineTo(x + 224 * s, y + 234 * s);
    ctx.lineTo(x + 324 * s, y + 234 * s);
    ctx.lineTo(x + 300 * s, y + 296 * s);
    ctx.lineTo(x + 224 * s, y + 296 * s);
    ctx.lineTo(x + 224 * s, y + 396 * s);
    ctx.lineTo(x + 154 * s, y + 396 * s);
    ctx.closePath();
    ctx.fill();
    ctx.beginPath();
    ctx.arc(x + 358 * s, y + 356 * s, 24 * s, 0, Math.PI * 2);
    ctx.fillStyle = "#06B6D4";
    ctx.fill();
    ctx.beginPath();
    ctx.arc(x + 358 * s, y + 356 * s, 10 * s, 0, Math.PI * 2);
    ctx.fillStyle = "#090D16";
    ctx.fill();
  }
  ctx.restore();
}
