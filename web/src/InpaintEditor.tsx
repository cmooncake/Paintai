import { useRef, useState } from "react";
import { asset, post, type Creation, type Job } from "./api";
type Point = { x: number; y: number };

export default function InpaintEditor({
  creation,
  disabled,
  onSubmitted,
}: {
  creation: Creation;
  disabled: boolean;
  onSubmitted: (job: Job) => void;
}) {
  const [open, setOpen] = useState(false);
  const [regions, setRegions] = useState<Point[][]>([]);
  const [draft, setDraft] = useState<Point[]>([]);
  const [vertices, setVertices] = useState<Point[]>([]);
  const drawing = useRef<Point[] | null>(null);
  const [instructions, setInstructions] = useState("");
  const [strength, setStrength] = useState(0.6);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const key = useRef<string | null>(null);
  const point = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
      y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)),
    };
  };
  async function submit() {
    if (busy || disabled || !regions.length || !instructions.trim()) return;
    setBusy(true);
    setError("");
    key.current ||= crypto.randomUUID();
    try {
      const job = await post<Job>(
        `/painters/${creation.painter_id}/creations/${creation.id}/inpaint`,
        { regions, instructions, strength },
        key.current,
      );
      onSubmitted(job);
      setOpen(false);
      key.current = null;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="inpaint-editor">
      <button
        className="secondary"
        disabled={busy}
        onClick={() => setOpen(!open)}
      >
        圈选局部重绘
      </button>
      {open && (
        <div>
          <h3>圈选需要修改的区域</h3>
          <p>
            拖动画圈，或依次点击多边形顶点后按“完成圈选”。可圈多个区域。红色范围会重绘，圈外保留原图。原作不会覆盖。
          </p>
          <div className="inpaint-canvas">
            <img
              src={asset(creation.image_url!)}
              alt="待局部重绘的原图"
              draggable={false}
            />
            <svg
              aria-label="圈选重绘区域"
              viewBox="0 0 100 100"
              preserveAspectRatio="none"
              onPointerDown={(e) => {
                if (busy || regions.length >= 8) return;
                e.currentTarget.setPointerCapture(e.pointerId);
                drawing.current = [point(e)];
                setDraft(drawing.current);
              }}
              onPointerMove={(e) => {
                if (!drawing.current) return;
                if (drawing.current.length < 1000) {
                  drawing.current = [...drawing.current, point(e)];
                  setDraft(drawing.current);
                }
              }}
              onPointerUp={() => {
                if (drawing.current && drawing.current.length >= 3) {
                  setRegions([...regions, drawing.current]);
                  key.current = null;
                } else if (drawing.current?.length) {
                  setVertices([...vertices, drawing.current[0]]);
                }
                drawing.current = null;
                setDraft([]);
              }}
              onPointerCancel={() => {
                drawing.current = null;
                setDraft([]);
              }}
            >
              {[
                ...regions,
                ...(vertices.length ? [vertices] : []),
                ...(draft.length ? [draft] : []),
              ].map((r, i) => (
                <polygon
                  key={i}
                  points={r.map((p) => `${p.x * 100},${p.y * 100}`).join(" ")}
                  fill="rgba(240,50,70,.35)"
                  stroke="#e32950"
                  strokeWidth="0.3"
                />
              ))}
            </svg>
          </div>
          <p>
            已圈选 {regions.length} 个区域（最多 8
            个）。建议包含手指、关节和少量周围环境。
          </p>
          <button
            type="button"
            disabled={busy || vertices.length < 3 || regions.length >= 8}
            onClick={() => {
              setRegions([...regions, vertices]);
              setVertices([]);
              key.current = null;
            }}
          >
            完成圈选
          </button>{" "}
          <button
            type="button"
            disabled={busy || !regions.length}
            onClick={() => {
              setRegions(regions.slice(0, -1));
              key.current = null;
            }}
          >
            撤销圈选
          </button>{" "}
          <button
            type="button"
            disabled={busy || (!regions.length && !vertices.length)}
            onClick={() => {
              setRegions([]);
              setVertices([]);
              key.current = null;
            }}
          >
            清空圈选
          </button>
          <label>
            修改要求
            <textarea
              aria-label="局部修改要求"
              maxLength={1200}
              value={instructions}
              disabled={busy}
              onChange={(e) => {
                setInstructions(e.target.value);
                key.current = null;
              }}
              placeholder="例如：修正双手，每只手五根自然弯曲的手指，托住礼盒边缘。"
            />
          </label>
          <p>
            本地模型更擅长简短英文描述；这是本地蒙版重绘，不使用聊天中的内置图片工具。CPU
            可能需要十几分钟。
          </p>
          <label>
            重绘强度 {strength.toFixed(2)}
            <input
              aria-label="重绘强度"
              type="range"
              min="0.1"
              max="0.95"
              step="0.05"
              value={strength}
              disabled={busy}
              onChange={(e) => {
                setStrength(Number(e.target.value));
                key.current = null;
              }}
            />
          </label>
          {error && <p role="alert">{error}</p>}
          <button
            disabled={
              busy || disabled || !!vertices.length || !regions.length || !instructions.trim()
            }
            onClick={() => void submit()}
          >
            {busy ? "正在提交…" : "开始局部重绘，另存新作品"}
          </button>
        </div>
      )}
    </section>
  );
}
