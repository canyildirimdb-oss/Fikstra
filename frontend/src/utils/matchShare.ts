import type { ScoreBreakdown } from "../types";
import { formatPickWithTeam } from "./format";

export interface MatchSharePayload {
  matchId: number;
  league: string;
  startDateTr: string;
  startTimeTr: string;
  homeTeam: string;
  awayTeam: string;
  homeLogoUrl?: string | null;
  awayLogoUrl?: string | null;
  marketKey: string;
  selectionKey: string;
  iddaaOdd: number;
  confidenceScore: number;
  valueEdgePct?: number | null;
  oddsDropPct?: number | null;
  breakdown?: ScoreBreakdown | null;
}

export function getMatchTopReasonChips(
  breakdown?: ScoreBreakdown | null,
  valueEdgePct?: number | null,
  oddsDropPct?: number | null
): string[] {
  if (!breakdown) return [];
  const candidates: { ratio: number; label: string }[] = [
    {
      ratio: breakdown.value / 35,
      label:
        valueEdgePct != null && valueEdgePct >= 1
          ? `Global Değer +%${valueEdgePct.toFixed(0)}`
          : "Oran Değeri",
    },
    {
      ratio: breakdown.odds_move / 25,
      label: breakdown.smart_money
        ? "Akıllı Para Teyidi"
        : oddsDropPct != null && oddsDropPct >= 2
        ? `%${oddsDropPct.toFixed(0)} Oran Düşüşü`
        : "Oran Dengesi",
    },
    {
      ratio: breakdown.form_goals / 25,
      label: "Form & Gol Gücü",
    },
    {
      ratio: breakdown.injury / 15,
      label: breakdown.injury_warning ? "Eksik Analizi" : "Kadro Sağlam",
    },
  ];
  candidates.sort((a, b) => b.ratio - a.ratio);
  return candidates.slice(0, 2).map((c) => c.label);
}

function drawRoundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number
) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function truncateByWidth(
  ctx: CanvasRenderingContext2D,
  text: string,
  maxWidth: number
): string {
  if (ctx.measureText(text).width <= maxWidth) return text;
  let trimmed = text;
  while (trimmed.length > 1 && ctx.measureText(`${trimmed}…`).width > maxWidth) {
    trimmed = trimmed.slice(0, -1);
  }
  return `${trimmed}…`;
}

function teamInitials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length >= 2) {
    return (words[0][0] + words[1][0]).toUpperCase();
  }
  return name.slice(0, 2).toUpperCase();
}

async function loadCorsSafeBitmap(url?: string | null): Promise<ImageBitmap | null> {
  if (!url) return null;
  try {
    const resp = await fetch(url, { mode: "cors" });
    if (!resp.ok) return null;
    const blob = await resp.blob();
    return await createImageBitmap(blob);
  } catch {
    return null;
  }
}

function drawFikstraLogo(ctx: CanvasRenderingContext2D, x: number, y: number, size: number) {
  ctx.save();
  drawRoundRect(ctx, x, y, size, size, size * 0.22);
  const bg = ctx.createLinearGradient(x, y, x + size, y + size);
  bg.addColorStop(0, "#0D1424");
  bg.addColorStop(1, "#090D16");
  ctx.fillStyle = bg;
  ctx.fill();
  ctx.strokeStyle = "rgba(16, 185, 129, 0.35)";
  ctx.lineWidth = 1.5;
  ctx.stroke();

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
  ctx.restore();
}

function drawTeamBadge(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  size: number,
  name: string,
  bitmap: ImageBitmap | null
) {
  ctx.save();
  drawRoundRect(ctx, x, y, size, size, 14);
  ctx.fillStyle = "#090D16";
  ctx.fill();
  ctx.strokeStyle = "rgba(255, 255, 255, 0.12)";
  ctx.lineWidth = 1.5;
  ctx.stroke();
  ctx.clip();

  if (bitmap) {
    const pad = 6;
    ctx.drawImage(bitmap, x + pad, y + pad, size - pad * 2, size - pad * 2);
  } else {
    ctx.fillStyle = "#34D399";
    ctx.font = "800 16px Inter, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(teamInitials(name), x + size / 2, y + size / 2 + 1);
  }
  ctx.restore();
}

export async function generateMatchSharePng(payload: MatchSharePayload): Promise<{
  blob: Blob;
  dataUrl: string;
  filename: string;
}> {
  if (document.fonts?.ready) {
    await document.fonts.ready;
  }

  const size = 540;
  const scale = 2; // 1080x1080 output
  const canvas = document.createElement("canvas");
  canvas.width = size * scale;
  canvas.height = size * scale;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas context unavailable");
  ctx.scale(scale, scale);

  // 1. Dark night-black background + emerald radial glow
  const bg = ctx.createLinearGradient(0, 0, size, size);
  bg.addColorStop(0, "#05080D");
  bg.addColorStop(0.55, "#090D16");
  bg.addColorStop(1, "#0D1424");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, size, size);

  const glow = ctx.createRadialGradient(270, 210, 20, 270, 210, 280);
  glow.addColorStop(0, "rgba(0, 255, 133, 0.14)");
  glow.addColorStop(0.6, "rgba(6, 182, 212, 0.04)");
  glow.addColorStop(1, "rgba(9, 13, 22, 0)");
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, size, size);

  // Outer card frame
  drawRoundRect(ctx, 18, 18, size - 36, size - 36, 26);
  ctx.fillStyle = "rgba(11, 17, 28, 0.65)";
  ctx.fill();
  ctx.strokeStyle = "rgba(16, 185, 129, 0.25)";
  ctx.lineWidth = 1.5;
  ctx.stroke();

  // Load team logos in parallel
  const [homeBmp, awayBmp] = await Promise.all([
    loadCorsSafeBitmap(payload.homeLogoUrl),
    loadCorsSafeBitmap(payload.awayLogoUrl),
  ]);

  const padX = 38;

  // 2. Top row: League & Date-Time
  ctx.font = "700 12px Inter, system-ui, sans-serif";
  ctx.fillStyle = "#94A3B8";
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  const leagueText = truncateByWidth(ctx, payload.league.toUpperCase(), 280);
  ctx.fillText(leagueText, padX, 48);

  const dateTimeStr = `${payload.startDateTr} · ${payload.startTimeTr}`;
  ctx.font = "600 12px 'JetBrains Mono', monospace";
  ctx.fillStyle = "#34D399";
  ctx.textAlign = "right";
  ctx.fillText(dateTimeStr, size - padX, 48);

  // Divider
  ctx.strokeStyle = "rgba(255, 255, 255, 0.07)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(padX, 68);
  ctx.lineTo(size - padX, 68);
  ctx.stroke();

  // 3. Teams row (logos + truncated names)
  const teamRowY = 92;
  drawTeamBadge(ctx, padX, teamRowY, 44, payload.homeTeam, homeBmp);
  drawTeamBadge(ctx, size - padX - 44, teamRowY, 44, payload.awayTeam, awayBmp);

  ctx.textBaseline = "middle";
  ctx.font = "800 16px Inter, system-ui, sans-serif";
  ctx.fillStyle = "#FFFFFF";
  ctx.textAlign = "left";
  ctx.fillText(truncateByWidth(ctx, payload.homeTeam, 155), padX + 54, teamRowY + 22);

  ctx.textAlign = "right";
  ctx.fillText(truncateByWidth(ctx, payload.awayTeam, 155), size - padX - 54, teamRowY + 22);

  ctx.font = "700 12px Inter, system-ui, sans-serif";
  ctx.fillStyle = "#64748B";
  ctx.textAlign = "center";
  ctx.fillText("vs", size / 2, teamRowY + 22);

  // 4. Confidence Ring (3 green tiers: 80+, 70-79, 60-69)
  const score = Math.round(payload.confidenceScore);
  const ringColor =
    score >= 80 ? "#00FF85" : score >= 70 ? "#10B981" : "#059669";
  const ringCx = size / 2;
  const ringCy = 224;
  const ringR = 54;

  ctx.save();
  if (score >= 80) {
    ctx.shadowColor = "rgba(0, 255, 133, 0.45)";
    ctx.shadowBlur = 18;
  }
  ctx.beginPath();
  ctx.arc(ringCx, ringCy, ringR, 0, Math.PI * 2);
  ctx.strokeStyle = "rgba(255, 255, 255, 0.08)";
  ctx.lineWidth = 8;
  ctx.stroke();

  const pct = Math.max(0, Math.min(100, payload.confidenceScore)) / 100;
  ctx.beginPath();
  ctx.arc(ringCx, ringCy, ringR, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * pct);
  ctx.strokeStyle = ringColor;
  ctx.lineWidth = 8;
  ctx.lineCap = "round";
  ctx.stroke();
  ctx.restore();

  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.font = "800 32px 'JetBrains Mono', monospace";
  ctx.fillStyle = "#FFFFFF";
  ctx.fillText(`%${score}`, ringCx, ringCy - 4);

  ctx.font = "700 10px Inter, system-ui, sans-serif";
  ctx.fillStyle = ringColor;
  ctx.fillText("GÜVEN PUANI", ringCx, ringCy + 22);

  // 5. Pick + Real Odd Pill
  const pickLabel = formatPickWithTeam(
    payload.marketKey,
    payload.selectionKey,
    payload.homeTeam,
    payload.awayTeam
  );
  const oddLabel = payload.iddaaOdd.toFixed(2);
  ctx.font = "800 16px Inter, system-ui, sans-serif";
  const pickW = ctx.measureText(pickLabel).width;
  ctx.font = "800 16px 'JetBrains Mono', monospace";
  const oddW = ctx.measureText(oddLabel).width;
  const pillW = Math.min(size - padX * 2, pickW + oddW + 58);
  const pillX = (size - pillW) / 2;
  const pillY = 304;

  drawRoundRect(ctx, pillX, pillY, pillW, 42, 21);
  ctx.fillStyle = "rgba(0, 255, 133, 0.14)";
  ctx.fill();
  ctx.strokeStyle = "rgba(0, 255, 133, 0.5)";
  ctx.lineWidth = 1.5;
  ctx.stroke();

  ctx.font = "800 15px Inter, system-ui, sans-serif";
  ctx.fillStyle = "#00FF85";
  ctx.textAlign = "left";
  const maxPickTextW = pillW - oddW - 48;
  ctx.fillText(truncateByWidth(ctx, pickLabel, maxPickTextW), pillX + 18, pillY + 21);

  ctx.font = "800 15px 'JetBrains Mono', monospace";
  ctx.fillStyle = "#FFFFFF";
  ctx.textAlign = "right";
  ctx.fillText(oddLabel, pillX + pillW - 18, pillY + 21);

  // 6. Top 2 real reason chips from breakdown
  const chips = getMatchTopReasonChips(
    payload.breakdown,
    payload.valueEdgePct,
    payload.oddsDropPct
  );
  if (chips.length > 0) {
    ctx.font = "600 12px Inter, system-ui, sans-serif";
    const chipWidths = chips.map((c) => ctx.measureText(c).width + 26);
    const gap = 10;
    const totalChipW = chipWidths.reduce((a, b) => a + b, 0) + gap * (chips.length - 1);
    let cx = (size - totalChipW) / 2;
    const cy = 366;
    chips.forEach((chip, i) => {
      const cw = chipWidths[i];
      drawRoundRect(ctx, cx, cy, cw, 30, 15);
      ctx.fillStyle = "rgba(255, 255, 255, 0.05)";
      ctx.fill();
      ctx.strokeStyle = "rgba(255, 255, 255, 0.12)";
      ctx.lineWidth = 1;
      ctx.stroke();

      ctx.fillStyle = "#CBD5E1";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(chip, cx + cw / 2, cy + 15);
      cx += cw + gap;
    });
  }

  // 7. Footer: Fikstra logo + domain + 18+ disclaimer
  const footY = 442;
  ctx.strokeStyle = "rgba(16, 185, 129, 0.2)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(padX, footY);
  ctx.lineTo(size - padX, footY);
  ctx.stroke();

  drawFikstraLogo(ctx, padX, footY + 18, 34);

  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.font = "800 16px Inter, system-ui, sans-serif";
  ctx.fillStyle = "#FFFFFF";
  ctx.fillText("Fikstra", padX + 44, footY + 27);

  ctx.font = "600 11px Inter, system-ui, sans-serif";
  ctx.fillStyle = "#34D399";
  ctx.fillText("Veriyle Okunan Maçlar · fikstra.onrender.com", padX + 44, footY + 45);

  ctx.textAlign = "center";
  ctx.font = "500 10px Inter, system-ui, sans-serif";
  ctx.fillStyle = "#64748B";
  ctx.fillText("Tahmindir, garanti değildir · 18+", size / 2, size - 28);

  const blob = await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("PNG encode failed"))), "image/png");
  });
  const dataUrl = canvas.toDataURL("image/png");
  return {
    blob,
    dataUrl,
    filename: `fikstra-mac-${payload.matchId}.png`,
  };
}
