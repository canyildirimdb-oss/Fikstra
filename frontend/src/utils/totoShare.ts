import type { SportotoMatchItem, TotoProbs } from "../types";
import { drawOfficialLogoMark, loadOfficialLogoMark } from "./logoLoader";

export async function generateTotoPngBlob(params: {
  title: string;
  matches: SportotoMatchItem[];
  picksBySlot: Record<number, string>;
  columns: number;
  estimatedCost: number;
  probs: TotoProbs;
}): Promise<Blob> {
  const { title, matches, picksBySlot, columns, estimatedCost, probs } = params;
  const logoImg = await loadOfficialLogoMark();
  const width = 1080;
  const rowH = 54;
  const headerH = 190;
  const footerH = 110;
  const height = headerH + matches.length * rowH + footerH;

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas context unavailable");

  // Dark background
  const bg = ctx.createLinearGradient(0, 0, width, height);
  bg.addColorStop(0, "#05080D");
  bg.addColorStop(1, "#0A1320");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, width, height);

  // Header brand with official transparent Fikstra logo
  drawOfficialLogoMark(ctx, logoImg, 48, 34, 62);
  ctx.fillStyle = "#00FF85";
  ctx.font = "800 32px Inter, system-ui, sans-serif";
  ctx.fillText("FİKSTRA TOTO", 124, 64);

  ctx.fillStyle = "#E2E8F0";
  ctx.font = "700 22px Inter, system-ui, sans-serif";
  ctx.fillText(title, 124, 98);

  // Summary pills
  ctx.fillStyle = "rgba(0, 255, 133, 0.12)";
  ctx.fillRect(48, 122, width - 96, 48);
  ctx.strokeStyle = "rgba(0, 255, 133, 0.35)";
  ctx.strokeRect(48, 122, width - 96, 48);

  ctx.fillStyle = "#00FF85";
  ctx.font = "700 20px Inter, system-ui, sans-serif";
  ctx.fillText(
    `${columns} Kolon (${estimatedCost.toFixed(0)} ₺)   •   Beklenen: ${probs.expected_hits.toFixed(1)}/15   •   13+: %${probs.p13_plus.toFixed(1)}   •   15/15: %${probs.p15.toFixed(3)}`,
    68,
    153
  );

  // 15 Match rows
  matches.forEach((m, idx) => {
    const y = headerH + idx * rowH;
    ctx.fillStyle = idx % 2 === 0 ? "rgba(255,255,255,0.025)" : "rgba(255,255,255,0.01)";
    ctx.fillRect(48, y, width - 96, rowH - 4);

    ctx.fillStyle = "#94A3B8";
    ctx.font = "700 18px monospace";
    ctx.fillText(String(m.slot_no).padStart(2, "0"), 64, y + 33);

    ctx.fillStyle = "#F1F5F9";
    ctx.font = "600 19px Inter, system-ui, sans-serif";
    const matchName = `${m.home_team} - ${m.away_team}`;
    ctx.fillText(matchName.slice(0, 44), 112, y + 33);

    const code = picksBySlot[m.slot_no] || "1";
    const pillW = 92;
    const pillX = width - 48 - pillW - 14;
    ctx.fillStyle = code.length === 3 ? "rgba(245, 158, 11, 0.22)" : code.length === 2 ? "rgba(6, 182, 212, 0.22)" : "rgba(0, 255, 133, 0.20)";
    ctx.fillRect(pillX, y + 8, pillW, 34);
    ctx.fillStyle = code.length === 3 ? "#FCD34D" : code.length === 2 ? "#67E8F9" : "#00FF85";
    ctx.font = "800 20px monospace";
    ctx.textAlign = "center";
    ctx.fillText(code, pillX + pillW / 2, y + 32);
    ctx.textAlign = "left";
  });

  // Footer
  ctx.fillStyle = "#64748B";
  ctx.font = "600 16px Inter, system-ui, sans-serif";
  ctx.fillText("fikstra.onrender.com • Veri odaklı matematiksel kolon şablonu (18+)", 48, height - 42);

  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error("PNG oluşturulamadı"));
    }, "image/png");
  });
}
