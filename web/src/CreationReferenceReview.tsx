import { asset } from "./api";
import type { ReferenceReview } from "./ReferenceReviewPanel";

const labels: Record<string, string> = {
  linework: "线条",
  coloring: "上色",
  lighting: "光影",
  eyes: "眼睛画法",
  face: "脸部比例",
};

export default function CreationReferenceReview({
  painterId,
  reviews,
}: {
  painterId: string;
  reviews: ReferenceReview[];
}) {
  if (!reviews.length) return null;
  return (
    <section className="creation-reference-review">
      <h3>与官方／风格参考图对照</h3>
      <p>
        视觉模型同时查看参考图与本作品。人物属性服从度见上方原题评价；以下分数是
        0–4 的技法接近程度，不是相似度百分比，也不能证明学习前后有进步。
      </p>
      {reviews
        .slice()
        .reverse()
        .map((review, index) => (
          <details key={review.id} open={index === 0}>
            <summary>
              {review.profile_name} ·{" "}
              {review.status === "complete" ? "对照完成" : "对照未完成"}
            </summary>
            <p>
              {review.model} · {new Date(review.created_at).toLocaleString()}
            </p>
            {review.verification_note && (
              <p className="rating-note">
                <b>助手看图复核（独立于模型评分）：</b>
                {review.verification_note}
              </p>
            )}
            <div className="creation-reference-images">
              {review.references.map((ref, i) => (
                <a
                  key={ref.id}
                  target="_blank"
                  rel="noreferrer"
                  href={asset(
                    `/api/v1/painters/${painterId}/references/${ref.id}/image`,
                  )}
                >
                  <img
                    src={asset(
                      `/api/v1/painters/${painterId}/references/${ref.id}/image`,
                    )}
                    alt={`风格参考 ${i + 1}`}
                  />
                </a>
              ))}
            </div>
            {!review.references.length && (
              <p>迁移包未包含参考原作，以下历史评价仍保留。</p>
            )}
            {review.error && <p role="alert">{review.error}</p>}
            {review.results.map((result) => (
              <div key={result.entry_id}>
                {Object.entries(result.dimensions).map(([key, d]) => (
                  <section key={key}>
                    <h4>
                      {labels[key] || key} · {d.score} / 4
                      {d.valid === false ? "（证据待核验）" : ""}
                    </h4>
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
                    <p>
                      置信度：
                      {(
                        { low: "低", medium: "中", high: "高" } as Record<
                          string,
                          string
                        >
                      )[d.confidence] || d.confidence}
                    </p>
                    {d.warning && <p className="rating-note">{d.warning}</p>}
                  </section>
                ))}
                <p>
                  {result.mean == null
                    ? "存在证据问题或分项未完成，不计算风格均分。"
                    : `模型风格均分：${result.mean.toFixed(2)} / 4（未校准）`}
                </p>
              </div>
            ))}
          </details>
        ))}
    </section>
  );
}
