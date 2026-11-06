import { useEffect, useState } from "react";
import { asset, request } from "./api";
import "./lora.css";

type Arm = {
  id: string;
  label: string;
  step: number;
  reused: boolean;
  hair: string;
  clothing: string;
  style: string;
  validation_mse: number | null;
  image_url: string;
};
type Report = {
  id: string;
  title: string;
  conclusion: string;
  protocol: string;
  image_provenance: string;
  completed_steps: number;
  total_steps: number;
  technical_pass: boolean;
  style_pass: boolean;
  technical_check_count: number;
  findings: { dimension: string; finding: string }[];
  limitations: string[];
  arms: Arm[];
  references: { id: string; label: string; image_url: string }[];
};

export default function LoraPanel({
  painterId,
  painterName,
}: {
  painterId: string;
  painterName: string;
}) {
  const [reports, setReports] = useState<Report[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let disposed = false;
    setLoading(true);
    setError("");
    setReports([]);
    request<{ items: Report[] }>(`/painters/${painterId}/lora-reports`)
      .then((r) => {
        if (!disposed) setReports(r.items);
      })
      .catch((e: Error) => {
        if (!disposed) setError(e.message);
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [painterId, revision]);
  return (
    <section className="lora-panel" aria-label="LoRA 训练对照">
      <div className="lora-heading">
        <h2>{painterName}的 LoRA 实验</h2>
        <button onClick={() => setRevision((v) => v + 1)} disabled={loading}>
          刷新结果
        </button>
      </div>
      <p>查看已归档的训练检查点，比较画风变化与人物属性是否保持。</p>
      {loading && <p role="status">正在读取实验结果…</p>}
      {error && (
        <div role="alert" className="alert">
          {error}
          <button onClick={() => setRevision((v) => v + 1)}>重试</button>
        </div>
      )}
      {!loading && !error && !reports.length && (
        <div className="lora-empty">
          <h3>这位画师还没有归档的 LoRA 实验</h3>
          <p>实验完成并归档后，检查点图片和评价会显示在这里。</p>
        </div>
      )}
      {reports.map((report) => (
        <article key={report.id} className="lora-report">
          <h3>{report.title}</h3>
          <div className="lora-badges">
            <span>
              训练完成 · {report.completed_steps} / {report.total_steps} 步
            </span>
            <span>
              {report.technical_pass
                ? `技术核验通过 · ${report.technical_check_count} 项`
                : "技术核验未通过"}
            </span>
            <span className={report.style_pass ? "lora-pass" : "lora-caution"}>
              {report.style_pass ? "画风验收通过" : "画风验收未通过"}
            </span>
          </div>
          <progress
            aria-label="已归档训练进度"
            value={report.completed_steps}
            max={report.total_steps}
          />
          <p className="lora-conclusion">{report.conclusion}</p>
          <p>{report.protocol}</p>
          <div className={`lora-grid${report.arms.length === 3 ? " lora-grid-three" : ""}`}>
            {report.arms.map((arm) => (
              <figure key={arm.id}>
                <figcaption>
                  <strong>{arm.label}</strong>
                  {arm.reused && <span>已验证复用</span>}
                </figcaption>
                <a
                  href={asset(arm.image_url)}
                  target="_blank"
                  rel="noreferrer"
                  aria-label={`查看${arm.label}原图`}
                >
                  <img
                    src={asset(arm.image_url)}
                    alt={`${painterName} · ${report.title} · ${arm.label}`}
                    loading="lazy"
                  />
                </a>
                <dl>
                  <dt>发色</dt>
                  <dd>{arm.hair}</dd>
                  <dt>服装</dt>
                  <dd>{arm.clothing}</dd>
                  <dt>画风</dt>
                  <dd>{arm.style}</dd>
                </dl>
                {arm.validation_mse !== null && (
                  <small>
                    验证去噪误差：{arm.validation_mse.toFixed(5)}
                    （不是画风相似度）
                  </small>
                )}
              </figure>
            ))}
          </div>
          <p className="lora-note">
            {report.image_provenance} 点击图片可查看原图。
          </p>
          <h4>画风与人物约束</h4>
          <dl className="lora-findings">
            {report.findings.map((finding) => (
              <div key={finding.dimension}>
                <dt>{finding.dimension}</dt>
                <dd>{finding.finding}</dd>
              </div>
            ))}
          </dl>
          {!!report.references.length && (
            <>
              <h4>参考作品 · 未参与本轮训练</h4>
              <div className="lora-references">
                {report.references.map((ref) => (
                  <figure key={ref.id}>
                    <a
                      href={asset(ref.image_url)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <img
                        src={asset(ref.image_url)}
                        alt={ref.label}
                        loading="lazy"
                      />
                    </a>
                    <figcaption>{ref.label}</figcaption>
                  </figure>
                ))}
              </div>
            </>
          )}
          <details>
            <summary>实验说明与局限</summary>
            <ul>
              {report.limitations.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </details>
        </article>
      ))}
    </section>
  );
}
