import { useState } from "react";
import { asset, request, type Painter } from "./api";

type Result = {
  painter: Painter;
  notice: string;
  counts: Record<string, number>;
};

export default function TransferPanel({
  painter,
  onImported,
}: {
  painter?: Painter;
  onImported: (painter: Painter) => void;
}) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [file, setFile] = useState<File | null>(null);
  async function download() {
    if (!painter) return;
    setBusy("export");
    setError("");
    setMessage("");
    try {
      const response = await fetch(
        asset(`/api/v1/painters/${painter.painter_id}/export`),
      );
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || "导出失败，请稍后重试。");
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = `paintai-${painter.painter_id}.zip`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 60000);
      setMessage("迁移包已准备下载。请将 ZIP 带到新电脑，在此页面导入。");
    } catch (e) {
      setError(e instanceof Error ? e.message : "导出失败。");
    } finally {
      setBusy("");
    }
  }
  async function restore() {
    if (!file) return;
    setBusy("import");
    setError("");
    setMessage("");
    try {
      if (file.size > 256 * 1024 * 1024)
        throw new Error("迁移包不能超过 256 MB。");
      const result = await request<Result>("/painters/import", {
        method: "POST",
        headers: { "Content-Type": "application/zip" },
        body: file,
      });
      onImported(result.painter);
      setMessage(
        `已恢复 ${result.painter.name}：${result.counts.creations} 件作品、${result.counts.learning_rounds} 轮学习、${result.counts.experiences} 条记忆、${result.counts.growth_experiments} 组成长对照和 ${result.counts.lora_reports} 份 LoRA 报告。可到作品库、学习和 LoRA 实验查看。`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "导入失败。");
    } finally {
      setBusy("");
    }
  }
  return (
    <section className="transfer-panel" aria-labelledby="transfer-title">
      <h2 id="transfer-title">数据迁移</h2>
      <p>把画师的成长带到另一台电脑。无需模型服务即可导入和浏览历史记录。</p>
      <div className="transfer-cards">
        <article>
          <h3>导出画师</h3>
          <p>
            {painter ? `当前画师：${painter.name}` : "请先选择或创建画师。"}
          </p>
          <p>
            包含长期目标、课程与学习进度、创作记忆、蒸馏经验、文字风格档案、作品图片、评价和实验报告。
          </p>
          <button
            className="primary"
            disabled={!painter || !!busy}
            onClick={() => void download()}
          >
            {busy === "export" ? "正在打包…" : "下载迁移包"}
          </button>
        </article>
        <article>
          <h3>导入画师</h3>
          <p>
            选择之前导出的 ZIP（最大 256
            MB）。恢复为原画师；同一画师已存在时不覆盖、不合并。
          </p>
          <label>
            选择迁移包
            <input
              type="file"
              accept=".zip,application/zip"
              disabled={!!busy}
              onChange={(e) => {
                setFile(e.target.files?.[0] || null);
                setError("");
                setMessage("");
              }}
            />
          </label>
          <button
            className="primary"
            disabled={!file || !!busy}
            onClick={() => void restore()}
          >
            {busy === "import" ? "正在校验并恢复…" : "导入并恢复"}
          </button>
        </article>
      </div>
      <p>
        不含 API 密钥、模型设置、底模／LoRA
        权重、参考原作或待执行任务。历史参考图评价保留；再次使用参考图需重新上传并学习。继续生成或训练前，请在新电脑配置模型。
      </p>
      {error && (
        <div className="alert" role="alert">
          {error}
        </div>
      )}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
