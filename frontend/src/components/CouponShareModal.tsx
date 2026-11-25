import { useEffect, useState } from "react";
import { Check, Copy, Download, Share2, X } from "lucide-react";
import { generateCouponSharePng, type ShareCouponPayload } from "../utils/couponShare";

interface Props {
  payload: ShareCouponPayload | null;
  onClose: () => void;
}

export function CouponShareModal({ payload, onClose }: Props) {
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [pngBlob, setPngBlob] = useState<Blob | null>(null);
  const [filename, setFilename] = useState<string>("fikstra-kupon.png");
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [copiedText, setCopiedText] = useState(false);

  useEffect(() => {
    if (!payload) {
      setPreviewUrl(null);
      setPngBlob(null);
      return;
    }
    let cancelled = false;
    setStatusMsg(null);
    generateCouponSharePng(payload)
      .then((res) => {
        if (cancelled) return;
        setPreviewUrl(res.dataUrl);
        setPngBlob(res.blob);
        setFilename(res.filename);
      })
      .catch(() => {
        if (!cancelled) setStatusMsg("Görsel oluşturulurken bir hata oluştu.");
      });
    return () => {
      cancelled = true;
    };
  }, [payload]);

  if (!payload) return null;

  const handleDownload = () => {
    if (!previewUrl) return;
    const a = document.createElement("a");
    a.href = previewUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setStatusMsg("PNG görseli indirildi.");
  };

  const handleNativeShare = async () => {
    if (!pngBlob) return;
    const file = new File([pngBlob], filename, { type: "image/png" });
    if (navigator.share && navigator.canShare?.({ files: [file] })) {
      try {
        await navigator.share({
          files: [file],
          title: `Fikstra • ${payload.title}`,
          text: `${payload.title} (${payload.totalOdd.toFixed(2)} Oran)`,
        });
        return;
      } catch (err) {
        if ((err as Error)?.name === "AbortError") return;
      }
    }
    // Fallback: copy image to clipboard if supported, else download
    try {
      if (navigator.clipboard && typeof ClipboardItem !== "undefined") {
        await navigator.clipboard.write([new ClipboardItem({ "image/png": pngBlob })]);
        setStatusMsg("Görsel panoya kopyalandı, doğrudan yapıştırabilirsiniz.");
        return;
      }
    } catch {
      // fall through to download
    }
    handleDownload();
  };

  const handleCopyText = () => {
    navigator.clipboard.writeText(payload.textPayload);
    setCopiedText(true);
    setTimeout(() => setCopiedText(false), 1800);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-3xl bg-[#131926] border border-[#1F2937] p-5 space-y-4 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-bold text-white">Kuponu Paylaş</h3>
          <button
            onClick={onClose}
            className="p-1.5 rounded-xl bg-[#0B0F17] border border-[#1F2937] text-slate-400 hover:text-white"
            aria-label="Kapat"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="rounded-2xl overflow-hidden bg-[#090D16] border border-[#1F2937] flex items-center justify-center min-h-[220px]">
          {previewUrl ? (
            <img src={previewUrl} alt="Fikstra Kupon Önizleme" className="w-full h-auto block" />
          ) : (
            <span className="text-xs text-slate-400">Kupon görseli hazırlanıyor...</span>
          )}
        </div>

        {statusMsg && <p className="text-xs text-emerald-400 text-center">{statusMsg}</p>}

        <div className="flex flex-col gap-2">
          <div className="grid grid-cols-2 gap-2">
            <button
              onClick={handleNativeShare}
              disabled={!pngBlob}
              className="inline-flex items-center justify-center gap-1.5 px-3 py-2.5 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-slate-950 text-xs font-bold disabled:opacity-50"
            >
              <Share2 className="w-4 h-4" />
              Paylaş
            </button>
            <button
              onClick={handleDownload}
              disabled={!previewUrl}
              className="inline-flex items-center justify-center gap-1.5 px-3 py-2.5 rounded-xl bg-cyan-500/15 border border-cyan-500/40 hover:bg-cyan-500/25 text-cyan-300 text-xs font-bold disabled:opacity-50"
            >
              <Download className="w-4 h-4" />
              Görseli İndir (PNG)
            </button>
          </div>

          <button
            onClick={handleCopyText}
            className="inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-xl bg-[#0B0F17] border border-[#1F2937] hover:border-slate-600 text-xs font-semibold text-slate-300"
          >
            {copiedText ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
            {copiedText ? "Metin Kopyalandı" : "Metin Kopyala"}
          </button>
        </div>
      </div>
    </div>
  );
}
