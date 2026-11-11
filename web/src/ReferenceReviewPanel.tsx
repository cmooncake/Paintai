import { asset } from "./api";

const labels: Record<string, string> = {
  linework: "线条",
  coloring: "上色",
  lighting: "光影",
  eyes: "眼睛画法",
  face: "脸部比例",
};
export type ReferenceReview = {
  id: string;
  status: string;
  created_at: string;
  model: string;
  profile_name: string;
  error?: string;
  verification_note?: string;
  references: { id: string; sha256: string }[];
  results: {
    entry_id: string;
    pair: number;
    arm: string;
    seed: number | null;
    status: string;
    mean?: number;
    dimensions: Record<
      string,
      {
        score: number;
        confidence: string;
        valid?: boolean;
        warning?: string | null;
        reference_evidence: string;
        generated_evidence: string;
        gap: string;
      }
    >;
  }[];
};

export default function ReferenceReviewPanel({
  painterId,
  reviews,
  disabled,
  start,
}: {
  painterId: string;
  reviews: ReferenceReview[];
  disabled: boolean;
  start: () => void;
}) {
  const latest = reviews.at(-1);
  function report(review: ReferenceReview) {
    const pairs = [...new Set(review.results.map((r) => r.pair))].sort(
      (a, b) => a - b,
    );
    const complete = review.results.filter((r) => r.status === "complete");
    const validPairs = pairs
      .map((pair) => ({
        b: complete.find((r) => r.pair === pair && r.arm === "baseline"),
        a: complete.find((r) => r.pair === pair && r.arm === "learned"),
      }))
      .filter((p) => p.a?.mean != null && p.b?.mean != null);
    return (
      <>
        <p>
          {review.profile_name} · {review.model} ·{" "}
          {new Date(review.created_at).toLocaleString()} ·
          {review.status === "complete"
            ? "已完成"
            : review.status === "running"
              ? "评价中"
              : "未完成"}
          （
          {review.results.reduce(
            (n, r) => n + Object.keys(r.dimensions).length,
            0,
          )}{" "}
          个分项已保存）
        </p>
        {review.error && <p role="alert">{review.error}</p>}
        <div className="growth-references">
          {review.references.map((r, i) => (
            <img
              key={r.id}
              src={asset(
                `/api/v1/painters/${painterId}/references/${r.id}/image`,
              )}
              alt={`对照参考 ${i + 1}`}
            />
          ))}
        </div>
        <p>
          0 明显不同 · 1 弱接近 · 2 部分接近 · 3 较接近 · 4
          很接近。数字不是百分比，也不是经过校准的相似度。†
          表示证据问题，原始分数保留但不计入均分。未标记也不保证判断准确。
        </p>
        <div className="growth-table">
          <table aria-label="参考画风分项对照">
            <thead>
              <tr>
                <th>种子</th>
                {Object.values(labels).map((t) => (
                  <th key={t}>
                    {t}
                    <br />
                    基线 → 学习
                  </th>
                ))}
                <th>均分差</th>
              </tr>
            </thead>
            <tbody>
              {pairs.map((pair) => {
                const b = review.results.find(
                  (r) => r.pair === pair && r.arm === "baseline",
                );
                const a = review.results.find(
                  (r) => r.pair === pair && r.arm === "learned",
                );
                return (
                  <tr key={pair}>
                    <th>{b?.seed ?? a?.seed ?? "未控制"}</th>
                    {Object.keys(labels).map((k) => (
                      <td key={k}>
                        {b?.dimensions[k]?.score ?? "—"}
                        {b?.dimensions[k]?.valid === false ? "†" : ""} →{" "}
                        {a?.dimensions[k]?.score ?? "—"}
                        {a?.dimensions[k]?.valid === false ? "†" : ""}
                      </td>
                    ))}
                    <td>
                      {a?.mean != null && b?.mean != null
                        ? `${a.mean - b.mean >= 0 ? "+" : ""}${(a.mean - b.mean).toFixed(2)}`
                        : a?.status === "complete" && b?.status === "complete"
                          ? "证据不足"
                          : "等待配对"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {validPairs.length > 0 && (
          <p>
            五项均未被规则标记的配对 {validPairs.length} 对；模型风格分均差：
            {(
              validPairs.reduce((n, p) => n + p.a!.mean! - p.b!.mean!, 0) /
              validPairs.length
            ).toFixed(2)}{" "}
            / 4。 仅为本视觉模型的探索性判断，不能据此宣称学会目标画风。
          </p>
        )}
        <div className="reference-work-list">
          {review.results.map((r) => (
            <details key={r.entry_id + r.arm}>
              <summary>
                种子 {r.seed} · {r.arm === "baseline" ? "基线" : "学习组"} ·{" "}
                {r.mean == null
                  ? r.status === "complete"
                    ? "存在证据问题，不计算均分"
                    : "评价未完成"
                  : `风格均分 ${r.mean.toFixed(2)} / 4`}{" "}
                · 查看证据与差距
              </summary>
              <img
                className="reference-target"
                src={asset(
                  `/api/v1/painters/${painterId}/creations/${r.entry_id}/image`,
                )}
                alt={`对照目标：种子 ${r.seed} ${r.arm === "baseline" ? "基线" : "学习组"}`}
              />
              {Object.entries(r.dimensions).map(([key, d]) => (
                <section key={key}>
                  <h5>
                    {labels[key]} · {d.score} / 4 · 置信度{" "}
                    {
                      (
                        { low: "低", medium: "中", high: "高" } as Record<
                          string,
                          string
                        >
                      )[d.confidence]
                    }
                  </h5>
                  <p>
                    <b>参考图：</b>
                    {d.reference_evidence}
                  </p>
                  <p>
                    <b>生成图：</b>
                    {d.generated_evidence}
                  </p>
                  <p>
                    <b>差距：</b>
                    {d.gap}
                  </p>
                  {d.warning && <p className="alert">{d.warning}</p>}
                </section>
              ))}
            </details>
          ))}
        </div>
      </>
    );
  }
  return (
    <section className="reference-review-panel">
      <h4>参考图对照评价</h4>
      <p>
        视觉模型同时查看冻结参考图与每张生成图，忽略角色、发色和服装颜色等内容差异，分项比较画风。原题服从度仍查看原有逐项评价。
      </p>
      <button disabled={disabled} onClick={start}>
        {latest
          ? "重新进行参考图对照（保留历史）"
          : "对本实验作品进行参考图对照"}
      </button>
      {latest ? report(latest) : <p>尚未进行多图对照。</p>}
      {reviews.length > 1 && (
        <details>
          <summary>历史参考图评价 · {reviews.length - 1} 次</summary>
          {reviews
            .slice(0, -1)
            .reverse()
            .map((r) => (
              <section key={r.id}>{report(r)}</section>
            ))}
        </details>
      )}
    </section>
  );
}
