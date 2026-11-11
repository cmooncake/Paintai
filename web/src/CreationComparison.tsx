import { asset, type Creation, type Critique } from "./api";

export type RevisionComparison = {
  seed: number | null;
  kind?: "image_edit";
  note: string;
  conclusion: string;
  baseline_id: string;
  revised_id: string;
  baseline_critique: Critique | null;
  revised_critique: Critique | null;
  rubric: string;
  checks?: { name: string; before: string; after: string }[];
};

export default function CreationComparison({
  creation,
}: {
  creation: Creation;
}) {
  const pairs = creation.revision_comparisons?.length
    ? creation.revision_comparisons
    : creation.revision_comparison
      ? [creation.revision_comparison]
      : [];
  if (!pairs.length) return null;
  return (
    <>
      {pairs
        .slice()
        .reverse()
        .map((pair, i) => (
          <details
            key={`${pair.baseline_id}-${pair.revised_id}`}
            open={i === 0}
          >
            <summary>
              {i === 0
                ? pair.kind === "image_edit"
                  ? "最新局部编辑对照"
                  : "最新同题对照"
                : `历史对照 ${i}`}
            </summary>
            <Pair creation={creation} pair={pair} />
          </details>
        ))}
    </>
  );
}

function Pair({
  creation,
  pair,
}: {
  creation: Creation;
  pair: RevisionComparison;
}) {
  return (
    <section className="creation-reference-review">
      <h3>
        {pair.kind === "image_edit"
          ? "局部图像编辑 · 修复前后对照"
          : "同题同种子 · 提示词修复对照"}
      </h3>
      <p>
        {pair.kind !== "image_edit" && <>种子 {pair.seed} · </>}
        {pair.note}
      </p>
      <div className="revision-images">
        {[
          {
            id: pair.baseline_id,
            title: "修复前",
            critique: pair.baseline_critique,
          },
          {
            id: pair.revised_id,
            title: "修复后",
            critique: pair.revised_critique,
          },
        ].map(({ id, title, critique }) => (
          <figure key={id}>
            <a
              href={asset(
                `/api/v1/painters/${creation.painter_id}/creations/${id}/image`,
              )}
              target="_blank"
              rel="noreferrer"
            >
              <img
                src={asset(
                  `/api/v1/painters/${creation.painter_id}/creations/${id}/image`,
                )}
                alt={`${title} · ${pair.kind === "image_edit" ? "局部图像编辑" : `同题种子 ${pair.seed}`}`}
              />
            </a>
            <figcaption>{title}</figcaption>
            {critique ? (
              <p>
                {pair.kind === "image_edit" ? "模型评价" : "同规则评价"}：主题 {critique.scores.topic} / 10，构图{" "}
                {critique.scores.composition} / 10
              </p>
            ) : (
              <p>等待同规则重评</p>
            )}
          </figure>
        ))}
      </div>
      <p className="rating-note">{pair.conclusion}</p>
      {!!pair.checks?.length && (
        <>
          <h4>助手看图逐项复核</h4>
          <table>
            <thead>
              <tr>
                <th>要求</th>
                <th>调整前</th>
                <th>调整后</th>
              </tr>
            </thead>
            <tbody>
              {pair.checks.map((check) => (
                <tr key={check.name}>
                  <th>{check.name}</th>
                  <td>{check.before}</td>
                  <td>{check.after}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      <small>
        评价规则：{pair.rubric}。
        {pair.kind === "image_edit"
          ? "本次是原图编辑，不能用来证明画师训练效果或底模进步。"
          : "本次检验提示词修复，不是 LoRA 训练效果实验；单个种子不能证明稳定改善。"}
      </small>
    </section>
  );
}
