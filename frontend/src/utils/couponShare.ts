import { formatPickWithTeam, formatTurkishDate } from "./format";
import { drawOfficialLogoMark, loadOfficialLogoMark } from "./logoLoader";

export interface ShareCouponLeg {
  league?: string;
  start_date_tr?: string;
  start_time_tr?: string;
  home_team: string;
  away_team: string;
  home_logo_url?: string | null;
  away_logo_url?: string | null;
  market_key: string;
  selection_key: string;
  iddaa_odd: number;
  confidence_score?: number;
  home_score?: number | null;
  away_score?: number | null;
  leg_result?: string;
}

export interface ShareCouponPayload {
  tier: "BANKO" | "DENGELI" | "SURPRIZ" | "CUSTOM";
  title: string;
  couponDate: string;
  totalOdd: number;
  avgConfidence?: number | null;
  couponStatus?: string;
  items: ShareCouponLeg[];
  textPayload: string;
}

const TIER_ACCENT: Record<string, { primary: string; soft: string; text: string }> = {
  BANKO: { primary: "#10B981", soft: "rgba(16, 185, 129, 0.16)", text: "#34D399" },
  DENGELI: { primary: "#06B6D4", soft: "rgba(6, 182, 212, 0.16)", text: "#22D3EE" },
  SURPRIZ: { primary: "#F59E0B", soft: "rgba(245, 158, 11, 0.16)", text: "#FBBF24" },
  CUSTOM: { primary: "#10B981", soft: "rgba(16, 185, 129, 0.16)", text: "#34D399" },
};

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

function drawTeamShield(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  size: number,
  name: string,
  bitmap: ImageBitmap | null,
  accent: string
) {
  ctx.save();
  drawRoundRect(ctx, x, y, size, size, 10);
  ctx.fillStyle = "#090D16";
  ctx.fill();
  ctx.strokeStyle = "#1F2937";
  ctx.lineWidth = 1.5;
  ctx.stroke();
  ctx.clip();

  if (bitmap) {
    const pad = 4;
    ctx.drawImage(bitmap, x + pad, y + pad, size - pad * 2, size - pad * 2);
  } else {
    ctx.fillStyle = accent;
    ctx.font = "700 13px Inter, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(teamInitials(name), x + size / 2, y + size / 2 + 1);
  }
  ctx.restore();
}

export async function generateCouponSharePng(
  payload: ShareCouponPayload
): Promise<{ dataUrl: string; blob: Blob; filename: string }> {
  const scale = 2;
  const width = 540;
  const headerH = 112;
  const rowH = 92;
  const rowGap = 12;
  const footerH = 84;
  const padX = 24;
  const height = headerH + payload.items.length * (rowH + rowGap) + footerH + 16;

  const canvas = document.createElement("canvas");
  canvas.width = width * scale;
  canvas.height = height * scale;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas 2D context unavailable");
  ctx.scale(scale, scale);

  const theme = TIER_ACCENT[payload.tier] || TIER_ACCENT.BANKO;

  // Premium dark navy gradient background
  const bg = ctx.createLinearGradient(0, 0, 0, height);
  bg.addColorStop(0, "#090D16");
  bg.addColorStop(1, "#0F172A");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, width, height);

  // Subtle outer frame
  drawRoundRect(ctx, 1, 1, width - 2, height - 2, 18);
  ctx.strokeStyle = "#1F2937";
  ctx.lineWidth = 2;
  ctx.stroke();

  // Top accent strip
  ctx.fillStyle = theme.primary;
  ctx.fillRect(0, 0, width, 5);

  // Header: Tier pill & date
  drawRoundRect(ctx, padX, 24, 176, 30, 8);
  ctx.fillStyle = theme.soft;
  ctx.fill();
  ctx.strokeStyle = theme.primary;
  ctx.lineWidth = 1;
  ctx.stroke();

  ctx.fillStyle = theme.text;
  ctx.font = "700 13px Inter, system-ui, sans-serif";
  ctx.textBaseline = "middle";
  ctx.fillText(payload.title, padX + 12, 39);

  if (payload.couponStatus === "WON" || payload.couponStatus === "LOST") {
    const isWon = payload.couponStatus === "WON";
    const stampColor = isWon ? "#00FF85" : "#FB7185";
    const stampSoft = isWon ? "rgba(0, 255, 133, 0.16)" : "rgba(244, 63, 94, 0.18)";
    drawRoundRect(ctx, padX + 186, 24, 96, 30, 8);
    ctx.fillStyle = stampSoft;
    ctx.fill();
    ctx.strokeStyle = stampColor;
    ctx.lineWidth = 1.2;
    ctx.stroke();

    ctx.fillStyle = stampColor;
    ctx.font = "800 12px Inter, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(isWon ? "KAZANDI" : "KAYBETTİ", padX + 186 + 48, 39);
    ctx.textAlign = "left";
  }

  ctx.fillStyle = "#94A3B8";
  ctx.font = "500 12px Inter, system-ui, sans-serif";
  ctx.fillText(formatTurkishDate(payload.couponDate), padX, 74);

  // Header right: Total odd
  ctx.textAlign = "right";
  ctx.fillStyle = "#94A3B8";
  ctx.font = "600 11px Inter, system-ui, sans-serif";
  ctx.fillText("TOPLAM ORAN", width - padX, 34);

  ctx.fillStyle = "#FFFFFF";
  ctx.font = "800 30px 'JetBrains Mono', monospace";
  ctx.fillText(payload.totalOdd.toFixed(2), width - padX, 68);
  ctx.textAlign = "left";

  // Preload CORS-safe logos & official Fikstra mark in parallel
  const [logoImg, bitmaps] = await Promise.all([
    loadOfficialLogoMark(),
    Promise.all(
      payload.items.map(async (it) => ({
        home: await loadCorsSafeBitmap(it.home_logo_url),
        away: await loadCorsSafeBitmap(it.away_logo_url),
      }))
    ),
  ]);

  // Match rows
  let curY = headerH;
  payload.items.forEach((it, idx) => {
    drawRoundRect(ctx, padX, curY, width - padX * 2, rowH, 12);
    ctx.fillStyle = "#131926";
    ctx.fill();
    ctx.strokeStyle = "#1F2937";
    ctx.lineWidth = 1;
    ctx.stroke();

    // League & date+time (GG.AA SS:DD)
    ctx.fillStyle = "#94A3B8";
    ctx.font = "500 11px Inter, system-ui, sans-serif";
    ctx.textAlign = "left";
    const leagueText = (it.league || "Maç").slice(0, 36);
    ctx.fillText(leagueText, padX + 14, curY + 18);

    const timeLabel = it.start_date_tr
      ? `${it.start_date_tr} ${it.start_time_tr || ""}`.trim()
      : it.start_time_tr || "";
    if (timeLabel) {
      ctx.textAlign = "right";
      ctx.fillStyle = "#94A3B8";
      ctx.font = "600 11px 'JetBrains Mono', monospace";
      ctx.fillText(timeLabel, width - padX - 14, curY + 18);
      ctx.textAlign = "left";
    }

    // Shields + Team names (+ optional final score on right)
    const hasScore = it.home_score != null && it.away_score != null;
    const bm = bitmaps[idx];
    drawTeamShield(ctx, padX + 14, curY + 30, 26, it.home_team, bm.home, theme.text);
    drawTeamShield(ctx, padX + 44, curY + 30, 26, it.away_team, bm.away, "#22D3EE");

    ctx.fillStyle = "#F1F5F9";
    ctx.font = "700 13px Inter, system-ui, sans-serif";
    const matchLabel = `${it.home_team} - ${it.away_team}`;
    const maxMatchW = hasScore ? width - padX * 2 - 150 : width - padX * 2 - 92;
    ctx.fillText(truncateByWidth(ctx, matchLabel, maxMatchW), padX + 78, curY + 43);

    if (hasScore) {
      ctx.textAlign = "right";
      ctx.fillStyle = "#E2E8F0";
      ctx.font = "800 13px 'JetBrains Mono', monospace";
      ctx.fillText(`${it.home_score} - ${it.away_score}`, width - padX - 14, curY + 43);
      ctx.textAlign = "left";
    }

    // Pick (with team name for MS 1/2) + Odd + Result/Confidence
    const pickStr = formatPickWithTeam(it.market_key, it.selection_key, it.home_team, it.away_team);
    ctx.fillStyle = theme.text;
    ctx.font = "700 13px Inter, system-ui, sans-serif";
    ctx.fillText(truncateByWidth(ctx, pickStr, width - padX * 2 - 180), padX + 14, curY + 72);

    ctx.textAlign = "right";
    if (it.leg_result === "WON" || it.leg_result === "LOST") {
      const legWon = it.leg_result === "WON";
      ctx.fillStyle = legWon ? "#00FF85" : "#FB7185";
      ctx.font = "800 11px Inter, system-ui, sans-serif";
      ctx.fillText(legWon ? "KAZANDI" : "KAYBETTİ", width - padX - 66, curY + 72);
    } else if (it.confidence_score != null) {
      ctx.fillStyle = "#94A3B8";
      ctx.font = "600 11px 'JetBrains Mono', monospace";
      ctx.fillText(`Güven ${Math.round(it.confidence_score)} Puan`, width - padX - 72, curY + 72);
    }
    ctx.fillStyle = "#FFFFFF";
    ctx.font = "800 14px 'JetBrains Mono', monospace";
    ctx.fillText(it.iddaa_odd.toFixed(2), width - padX - 14, curY + 72);
    ctx.textAlign = "left";

    curY += rowH + rowGap;
  });

  // Footer divider
  const footY = height - footerH + 8;
  ctx.strokeStyle = "#1F2937";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(padX, footY);
  ctx.lineTo(width - padX, footY);
  ctx.stroke();

  // Bottom-left: Official Fikstra logo + signature + tagline
  drawOfficialLogoMark(ctx, logoImg, padX, footY + 12, 28);
  ctx.fillStyle = "#F8FAFC";
  ctx.font = "800 14px Inter, system-ui, sans-serif";
  ctx.textBaseline = "middle";
  ctx.fillText("Fikstra", padX + 36, footY + 20);
  ctx.fillStyle = "#34D399";
  ctx.font = "500 10px Inter, system-ui, sans-serif";
  ctx.fillText("Veriyle Okunan Maçlar", padX + 36, footY + 34);

  // Bottom-right: Domain & Avg confidence
  ctx.textAlign = "right";
  ctx.fillStyle = "#E2E8F0";
  ctx.font = "600 11px 'JetBrains Mono', monospace";
  ctx.fillText("fikstra.onrender.com", width - padX, footY + 20);
  if (payload.avgConfidence != null) {
    ctx.fillStyle = "#94A3B8";
    ctx.font = "500 11px 'JetBrains Mono', monospace";
    ctx.fillText(`Ort. Güven: ${payload.avgConfidence.toFixed(1)} / 100`, width - padX, footY + 35);
  }

  // Disclaimer note
  ctx.textAlign = "center";
  ctx.fillStyle = "#64748B";
  ctx.font = "500 10px Inter, system-ui, sans-serif";
  ctx.fillText("Tahmindir, garanti değildir · 18+", width / 2, footY + 58);
  ctx.textAlign = "left";

  const dataUrl = canvas.toDataURL("image/png");
  const blob: Blob = await new Promise((resolve, reject) => {
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("PNG export failed"))), "image/png");
  });
  const slug = payload.tier.toLowerCase();
  const filename = `fikstra-${slug}-${payload.couponDate}.png`;
  return { dataUrl, blob, filename };
}
