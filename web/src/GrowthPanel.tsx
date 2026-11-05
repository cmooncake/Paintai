import { useEffect, useRef, useState } from "react";
import { asset, post, request } from "./api";
import type { Creation, Job, Critique } from "./api";
import "./growth.css";
import ReferenceReviewPanel from "./ReferenceReviewPanel";
import type { ReferenceReview } from "./ReferenceReviewPanel";

const dimensions: Record<string, string> = {
  topic: "主题表达",
  technique: "技法表现",
  composition: "画面构图",
  color: "色彩和谐",
  style: "风格描述符合度",
  creativity: "创意表达",
};
type Rule = {
  id: string;
  topic: string;
  rules: string[];
  sample_count: number;
  score_gap: number;
  evidence: {
    entry_id: string;
    score: number;
    feedback: string;
    plan: { prompt: string };
  }[];
  dimension_delta: Record<string, number>;
};
type Result = {
  pair: number;
  arm: "baseline" | "learned";
  entry_id: string;
  seed: number | null;
  critique: Critique;
  memory_used: { rules: string[] }[];
};
type Snapshot = {
  mode?: "memory" | "style_profile" | "image_reference";
  image_conditioning?: {
    reference_id: string;
    strength: number;
    method: string;
    sha256: string;
  };
  topic: string;
  pairs: number;
  memory_snapshot: { entry_id: string; source: string; rules: string[] }[];
  memory_digest: string;
  seed_controlled: boolean;
  base_seed: number | null;
  conditions: Record<
    string,
    { provider: string; model: string; parameters: Record<string, unknown> }
  >;
  painter_snapshot: {
    growth_goal: string;
    style: string;
    style_profile?: {
      name: string;
      reference_ids: string[];
    } | null;
  };
};
type Experiment = Snapshot & {
  reference_reviews?: ReferenceReview[];
  id: string;
  status: string;
  created_at: string;
  results: Result[];
  failed_entry_id?: string;
  visual_review?: {
    conclusion: string;
    reference_gap: string;
    pairs: { seed: number; assessment: string }[];
    repeatability?: {
      seed: number;
      previous_score: number;
      current_score: number;
      same_image: boolean;
    };
  };
  summary: {
    paired: number;
    conclusion: string;
    baseline?: number;
    learned?: number;
    delta: number | null;
    ci95: number[] | null;
    wins?: number;
    ties?: number;
    losses?: number;
    dimensions: Record<
      string,
      { baseline: number; learned: number; delta: number }
    >;
  };
};

function Curve({ results }: { results: Result[] }) {
  const count = Math.max(1, ...results.map((r) => r.pair + 1));
  const x = (i: number) => 36 + (count === 1 ? 0 : (i / (count - 1)) * 560);
  const y = (score: number) => 170 - ((score - 1) / 9) * 140;
  return (
    <svg
      className="growth-curve"
      viewBox="0 0 640 206"
      role="img"
      aria-label="逐对作品评分曲线：灰色为基线，绿色为学习组；完整数值见下方作品卡"
    >
      {[1, 5, 10].map((n) => (
        <g key={n}>
          <line x1="36" y1={y(n)} x2="605" y2={y(n)} stroke="#dce1d6" />
          <text x="12" y={y(n) + 4}>
            {n}
          </text>
        </g>
      ))}
      {(["baseline", "learned"] as const).map((arm) => {
        const points = results
          .filter((r) => r.arm === arm)
          .sort((a, b) => a.pair - b.pair);
        return (
          <g
            key={arm}
            stroke={arm === "learned" ? "#526f3d" : "#9c9588"}
            fill={arm === "learned" ? "#526f3d" : "#9c9588"}
          >
            <polyline
              fill="none"
              strokeWidth="2.5"
              points={points
                .map((r) => `${x(r.pair)},${y(r.critique.overall)}`)
                .join(" ")}
            />
            {points.map((r) => (
              <circle
                key={r.entry_id}
                cx={x(r.pair)}
                cy={y(r.critique.overall)}
                r="4"
              >
                <title>
                  第 {r.pair + 1} 对 · {arm === "learned" ? "学习组" : "基线"}{" "}
                  {r.critique.overall}
                </title>
              </circle>
            ))}
          </g>
        );
      })}
      {Array.from({ length: count }, (_, i) => (
        <text key={i} x={x(i)} y="194" textAnchor="middle">
          {i + 1}
        </text>
      ))}
    </svg>
  );
}

export default function GrowthPanel({
  painterId,
  running,
  ready,
  onJob,
  onOpen,
}: {
  painterId: string;
  running: Job | undefined;
  ready: boolean;
  onJob: (job: Job) => void;
  onOpen: (creation: Creation) => void;
}) {
  const [rules, setRules] = useState<Rule[]>([]);
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [topic, setTopic] = useState("");
  const [pairs, setPairs] = useState(3);
  const [mode, setMode] = useState("style_profile");
  const [referenceIndex, setReferenceIndex] = useState(0);
  const [referenceStrength, setReferenceStrength] = useState(0.6);
  const [preview, setPreview] = useState<Snapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const pending = useRef(false);
  const retry = useRef<{ body: string; key: string } | null>(null);
  useEffect(() => {
    let disposed = false;
    async function load() {
      try {
        const [a, b] = await Promise.all([
          request<{ items: Rule[] }>(`/painters/${painterId}/distilled`),
          request<{ items: Experiment[] }>(
            `/painters/${painterId}/experiments`,
          ),
        ]);
        if (!disposed) {
          setRules(a.items);
          setExperiments(b.items);
        }
      } catch (e) {
        if (!disposed) setError((e as Error).message);
      }
    }
    void load();
    const timer = setInterval(load, 2500);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [painterId]);
  async function action(fn: () => Promise<void>) {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  async function open(id: string) {
    await action(async () =>
      onOpen(await request<Creation>(`/painters/${painterId}/creations/${id}`)),
    );
  }
  async function start() {
    await action(async () => {
      const body = JSON.stringify({
        topic: topic.trim(),
        pairs,
        mode,
        reference_index: referenceIndex,
        reference_strength: referenceStrength,
      });
      if (retry.current?.body !== body)
        retry.current = { body, key: crypto.randomUUID() };
      onJob(
        await post<Job>(
          `/painters/${painterId}/experiments`,
          JSON.parse(body),
          retry.current.key,
        ),
      );
      retry.current = null;
      setPreview(null);
    });
  }
  return (
    <section className="growth-space">
      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
      <div className="growth-intro">
        <div className="eyebrow">EVIDENCE BEFORE CONFIDENCE</div>
        <h2>经验，让画变好了吗？</h2>
        <p>
          用同一道题，对比「不使用经验」与「使用当前学习经验」。这衡量上下文学习的效果，不是模型权重训练，也不是历史作品的简单前后排名。
        </p>
      </div>
      <section className="direction-card">
        <h3>提炼可复用的经验</h3>
        <p>
          自动比较同题、同目标、同模型条件下的真实作品。至少两幅且均分差 ≥ 0.25
          才形成候选规则；保留高低分反馈与构思，供你核对。规则仍需实验验证。
        </p>
        <button
          className="secondary"
          disabled={busy || !!running}
          onClick={() =>
            void action(async () => {
              const result = await post<{ items: Rule[]; scanned: number }>(
                `/painters/${painterId}/distilled`,
                {},
              );
              setRules(result.items);
              setNotice(
                `已检查最近 ${result.scanned} 幅完成作品，得到 ${result.items.length} 条候选经验。`,
              );
            })
          }
        >
          整理并蒸馏历史经验
        </button>
        {notice && <p role="status">{notice}</p>}
        {!rules.length && (
          <p>
            暂无足够的同题对比证据。可以重复创作同一主题；不同题目、模拟作品和实验作品不会混在一起蒸馏。
          </p>
        )}
        {rules.map((rule) => (
          <details className="distilled-rule" key={rule.id}>
            <summary>
              {rule.rules[0]}{" "}
              <small>
                候选 · {rule.sample_count} 幅 · 高低分差{" "}
                {rule.score_gap.toFixed(2)}
              </small>
            </summary>
            <p>{rule.topic}</p>
            <ul>
              {rule.rules.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
            <div className="growth-pair">
              {rule.evidence.map((e, i) => (
                <div key={e.entry_id}>
                  <h4>
                    {i === 0 ? "较高分来源" : "较低分来源"} ·{" "}
                    {e.score.toFixed(2)}
                  </h4>
                  <p>{e.feedback}</p>
                  <details>
                    <summary>原始生图提示词</summary>
                    <p>{e.plan.prompt}</p>
                  </details>
                  <button onClick={() => void open(e.entry_id)}>
                    查看来源作品 ↗
                  </button>
                </div>
              ))}
            </div>
            <p>
              这是从成对反馈中提取的练习建议，分数差不证明某条提示词造成了改善。
            </p>
          </details>
        ))}
      </section>
      <section className="direction-card">
        <h3>新建成长对照实验</h3>
        <label htmlFor="experiment-mode">对照内容</label>
        <select
          id="experiment-mode"
          value={mode}
          disabled={busy || !!running}
          onChange={(e) => {
            setMode(e.target.value);
            setPreview(null);
          }}
        >
          <option value="style_profile">参考风格：未使用 / 使用风格档案</option>
          <option value="image_reference">
            参考图生图：仅文字 / 文字 + 图片
          </option>
          <option value="memory">创作经验：未使用 / 使用记忆</option>
        </select>
        <p>
          {mode === "image_reference"
            ? "两组使用完全相同的题目、文字提示词与种子；学习组额外通过 IP-Adapter 接收一张参考图。关闭旧记忆和长期目标。参考图可能带入发色、服装和构图，需要核对偏题情况。"
            : "风格对照只使用本轮题目，暂不使用长期目标和旧记忆；学习组额外使用风格档案。两组按相同标准评价，单对结果仅作观察。"}
        </p>
        {mode === "image_reference" && (
          <>
            <label htmlFor="reference-index">当前风格档案中的参考图</label>
            <select
              id="reference-index"
              value={referenceIndex}
              disabled={busy || !!running}
              onChange={(e) => {
                setReferenceIndex(Number(e.target.value));
                setPreview(null);
              }}
            >
              {[0, 1, 2].map((i) => (
                <option key={i} value={i}>
                  参考 {i + 1}（以预览图片为准）
                </option>
              ))}
            </select>
            <label htmlFor="reference-strength">图片影响强度（0.1–1）</label>
            <input
              id="reference-strength"
              type="number"
              min="0.1"
              max="1"
              step="0.1"
              value={referenceStrength}
              disabled={busy || !!running}
              onChange={(e) => {
                setReferenceStrength(Number(e.target.value));
                setPreview(null);
              }}
            />
          </>
        )}
        <label htmlFor="experiment-topic">固定测试题目</label>
        <textarea
          id="experiment-topic"
          value={topic}
          maxLength={1000}
          disabled={busy || !!running}
          onChange={(e) => {
            setTopic(e.target.value);
            setPreview(null);
          }}
          placeholder="选择已有学习经验的主题，例如：A quiet beach at sunset"
        />
        <label htmlFor="experiment-pairs">配对轮数</label>
        <select
          id="experiment-pairs"
          value={pairs}
          disabled={busy || !!running}
          onChange={(e) => {
            setPairs(Number(e.target.value));
            setPreview(null);
          }}
        >
          {[1, 3, 5, 10].map((n) => (
            <option key={n} value={n}>
              {n} 对 · {n * 2} 幅作品{n === 1 ? "（流程试跑）" : ""}
            </option>
          ))}
        </select>
        <p>
          两组使用相同目标和模型配置，交替生成顺序；本地生图使用配对种子。冻结经验，不把实验结果写回训练记忆。至少
          3 对才报告探索性趋势，10 对更适合复验。
        </p>
        <button
          className="secondary"
          disabled={busy || !!running || !topic.trim()}
          onClick={() =>
            void action(async () =>
              setPreview(
                await post<Snapshot>(
                  `/painters/${painterId}/experiments/preview`,
                  {
                    topic: topic.trim(),
                    pairs,
                    mode,
                    reference_index: referenceIndex,
                    reference_strength: referenceStrength,
                  },
                ),
              ),
            )
          }
        >
          预览实验条件
        </button>
        {preview && (
          <div className="experiment-preview">
            <h4>本次将生成 {preview.pairs * 2} 幅作品</h4>
            <p>题目：{preview.topic}</p>
            <p>
              长期目标：{preview.painter_snapshot.growth_goal || "未设置"} ·
              风格：{preview.painter_snapshot.style}
            </p>
            <p>
              {preview.seed_controlled
                ? `生图配对种子从 ${preview.base_seed} 开始逐对递增`
                : "当前生图 API 无法控制种子，存在额外随机性"}
              。构思与评价服务仍可能有随机波动。
            </p>
            <p>
              冻结 {preview.memory_snapshot.length} 条经验：
              {preview.memory_snapshot
                .map((n) =>
                  n.source === "best_worst_extract_v1"
                    ? "成对蒸馏规则"
                    : "原始评价建议",
                )
                .join("、")}
            </p>
            <ul>
              {preview.memory_snapshot
                .flatMap((n) => n.rules.slice(0, 2))
                .map((r, i) => (
                  <li key={i}>{r.slice(0, 160)}</li>
                ))}
            </ul>
            <details>
              <summary>模型与参数</summary>
              <pre>{JSON.stringify(preview.conditions, null, 2)}</pre>
            </details>
            {preview.image_conditioning && (
              <div className="growth-references">
                <img
                  alt="将实际用于生图的参考作品"
                  src={asset(
                    `/api/v1/painters/${painterId}/references/${preview.image_conditioning.reference_id}/image`,
                  )}
                />
                <p>
                  IP-Adapter 图片强度：{preview.image_conditioning.strength}
                  ；基线不接收图片。
                </p>
              </div>
            )}
            <button
              className="primary"
              disabled={busy || !!running || !ready}
              onClick={() => void start()}
            >
              开始 {preview.pairs} 对实验
            </button>
            <p>
              提交时冻结最终条件，可在实验记录中核对；后台持续执行，离开页面不影响运行。
            </p>
          </div>
        )}
      </section>
      {running?.kind === "experiment" && (
        <p role="status" className="learning-help">
          对照实验进行中 · 已完成 {running.training?.completed || 0} /{" "}
          {running.training?.total} 幅
        </p>
      )}
      <h3>
        实验记录 <small>最近 {experiments.length} 次</small>
      </h3>
      {!experiments.length && (
        <p>完成实验后，这里会显示逐对作品、分数曲线与六维差异。</p>
      )}
      {experiments.map((exp) => (
        <article className="experiment-record" key={exp.id}>
          <header>
            <h3>{exp.topic}</h3>
            <span>
              {(
                {
                  running: "运行中",
                  complete: "已完成",
                  failed: "未完成",
                  interrupted: "已中断",
                } as Record<string, string>
              )[exp.status] || exp.status}{" "}
              · {exp.results.length}/{exp.pairs * 2} 幅 ·{" "}
              {new Date(exp.created_at).toLocaleString("zh-CN")}
            </span>
          </header>
          <div className="experiment-verdict">
            <strong>
              {exp.mode === "image_reference"
                ? "参考图生图对照 · "
                : exp.mode === "style_profile"
                  ? "风格档案对照 · "
                  : "经验对照 · "}
              {exp.visual_review?.conclusion || exp.summary.conclusion}
            </strong>
            <span>
              {exp.summary.delta === null
                ? "等待完整配对"
                : `学习组 − 基线：${exp.summary.delta >= 0 ? "+" : ""}${exp.summary.delta.toFixed(2)} 分`}
            </span>
          </div>
          {exp.image_conditioning && (
            <section className="style-evidence-note">
              <strong>仅文字 vs 文字 + 参考图</strong>
              <p>
                两组提示词与种子相同。学习组通过 IP-Adapter 接收下图，图片强度{" "}
                {exp.image_conditioning.strength}。
                参考图不是训练权重，也不是纯风格控制；仍可能迁移角色内容。单对只作观察。
              </p>
              <div className="growth-references">
                <img
                  alt="实际接入生图的参考作品"
                  src={asset(
                    `/api/v1/painters/${painterId}/references/${exp.image_conditioning.reference_id}/image`,
                  )}
                />
              </div>
            </section>
          )}
          {exp.mode === "style_profile" && (
            <section className="style-evidence-note">
              <strong>人物要求达标，不等于接近目标画风</strong>
              <p>
                当前方法把风格档案压缩成最多四个绘画技巧。生图没有直接使用参考图片，也没有训练模型权重；原有评价中的风格分只依据文字描述，不是参考作品的视觉相似度分数。下方新增的参考图对照评价单独展示多图比较证据。
              </p>
              <p>
                这组实验检验当前提示引导在不同种子下是否稳定，不能据此宣称已经学会目标画风。
              </p>
              {!!exp.painter_snapshot.style_profile?.reference_ids.length && (
                <details>
                  <summary>
                    查看本次冻结档案的目标参考：
                    {exp.painter_snapshot.style_profile.name}
                  </summary>
                  <div className="growth-references">
                    {exp.painter_snapshot.style_profile.reference_ids.map(
                      (id, i) => (
                        <img
                          key={id}
                          loading="lazy"
                          src={asset(
                            `/api/v1/painters/${painterId}/references/${id}/image`,
                          )}
                          alt={`目标参考作品 ${i + 1}`}
                        />
                      ),
                    )}
                  </div>
                </details>
              )}
            </section>
          )}
          <div className="growth-table">
            <table aria-label="逐种子配对汇总">
              <thead>
                <tr>
                  <th>种子</th>
                  <th>基线总分</th>
                  <th>学习组总分</th>
                  <th>差值</th>
                  <th>主题分：基线 → 学习</th>
                  <th>风格描述分：基线 → 学习</th>
                </tr>
              </thead>
              <tbody>
                {Array.from({ length: exp.pairs }, (_, pair) => {
                  const before = exp.results.find(
                    (r) => r.pair === pair && r.arm === "baseline",
                  );
                  const after = exp.results.find(
                    (r) => r.pair === pair && r.arm === "learned",
                  );
                  const delta =
                    before && after
                      ? after.critique.overall - before.critique.overall
                      : null;
                  return (
                    <tr key={pair}>
                      <th>
                        {before?.seed ??
                          after?.seed ??
                          (exp.seed_controlled && exp.base_seed !== null
                            ? (exp.base_seed + pair) % 2147483648
                            : "未控制")}
                      </th>
                      <td>{before?.critique.overall.toFixed(2) ?? "等待"}</td>
                      <td>{after?.critique.overall.toFixed(2) ?? "等待"}</td>
                      <td>
                        {delta === null
                          ? "等待配对"
                          : `${delta >= 0 ? "+" : ""}${delta.toFixed(2)}`}
                      </td>
                      <td>
                        {before?.critique.scores.topic?.toFixed(2) ?? "—"} →{" "}
                        {after?.critique.scores.topic?.toFixed(2) ?? "—"}
                      </td>
                      <td>
                        {before?.critique.scores.style?.toFixed(2) ?? "—"} →{" "}
                        {after?.critique.scores.style?.toFixed(2) ?? "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {(exp.pairs > 1 || exp.mode === "image_reference") && (
            <section>
              <h4>逐种子作品概览（左：基线，右：学习组）</h4>
              <div className="seed-overview">
                {Array.from({ length: exp.pairs }, (_, pair) => (
                  <div className="seed-preview" key={pair}>
                    <strong>
                      种子{" "}
                      {exp.results.find((r) => r.pair === pair)?.seed ??
                        (exp.base_seed !== null
                          ? (exp.base_seed + pair) % 2147483648
                          : "未控制")}
                    </strong>
                    <div>
                      {(["baseline", "learned"] as const).map((arm) => {
                        const result = exp.results.find(
                          (r) => r.pair === pair && r.arm === arm,
                        );
                        return result ? (
                          <button
                            className="growth-art"
                            key={arm}
                            onClick={() => void open(result.entry_id)}
                          >
                            <img
                              loading="lazy"
                              src={asset(
                                `/api/v1/painters/${painterId}/creations/${result.entry_id}/image`,
                              )}
                              alt={`种子 ${result.seed} · ${arm === "baseline" ? "基线" : "学习组"}`}
                            />
                            <span>
                              {arm === "baseline" ? "基线" : "学习组"} ·{" "}
                              {result.critique.overall.toFixed(2)}
                            </span>
                          </button>
                        ) : (
                          <span key={arm}>等待生成</span>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}
          {exp.status === "complete" && exp.painter_snapshot.style_profile && (
            <ReferenceReviewPanel
              painterId={painterId}
              reviews={exp.reference_reviews || []}
              disabled={busy || !!running || !ready}
              start={() =>
                void action(async () =>
                  onJob(
                    await post<Job>(
                      `/painters/${painterId}/experiments/${exp.id}/reference-review`,
                      {},
                      crypto.randomUUID(),
                    ),
                  ),
                )
              }
            />
          )}
          {exp.visual_review && (
            <section className="style-evidence-note">
              <h4>画面对照复核</h4>
              <p>
                <strong>{exp.visual_review.conclusion}</strong>
              </p>
              <p>{exp.visual_review.reference_gap}</p>
              <p>
                模型分数统计：{exp.summary.conclusion}
                。该统计不等于目标画风相似度结论。
              </p>
              {exp.visual_review.repeatability?.same_image && (
                <p>
                  同图复现检查：种子 {exp.visual_review.repeatability.seed}{" "}
                  的基线图片哈希、提示词和模型配置相同， 重复评分却从{" "}
                  {exp.visual_review.repeatability.previous_score.toFixed(2)}{" "}
                  变为{" "}
                  {exp.visual_review.repeatability.current_score.toFixed(2)}。
                  当前评分波动明显，不宜只凭分差判断进步。
                </p>
              )}
              <ul>
                {exp.visual_review.pairs.map((pair) => (
                  <li key={pair.seed}>
                    种子 {pair.seed}：{pair.assessment}
                  </li>
                ))}
              </ul>
              <small>
                助手看图判断，非盲评，不参与模型分数或置信区间计算。
              </small>
            </section>
          )}
          {exp.summary.paired > 0 && (
            <>
              <div className="growth-metrics">
                <span>
                  基线均分 <b>{exp.summary.baseline?.toFixed(2)}</b>
                </span>
                <span>
                  学习组均分 <b>{exp.summary.learned?.toFixed(2)}</b>
                </span>
                <span>
                  胜 / 平 / 负{" "}
                  <b>
                    {exp.summary.wins} / {exp.summary.ties} /{" "}
                    {exp.summary.losses}
                  </b>
                </span>
              </div>
              <p className="growth-caption">
                仅统计 {exp.summary.paired} 个完整配对。
                {exp.summary.ci95
                  ? `配对均差的探索性 95% 区间：[${exp.summary.ci95.join(", ")}]。`
                  : "样本不足或差值无变化，不报告置信区间。"}
                同一模型评分可能偏宽松，结果不等于人类审美判断或跨题泛化能力。
              </p>
              <div className="curve-legend">
                <span>● 基线</span>
                <span>● 学习组</span>
                <span>横轴：配对轮次 · 纵轴：模型评分</span>
              </div>
              <Curve results={exp.results} />
              <div className="growth-table">
                <table>
                  <thead>
                    <tr>
                      <th>评价维度</th>
                      <th>基线</th>
                      <th>学习组</th>
                      <th>变化</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(exp.summary.dimensions).map(([key, v]) => (
                      <tr key={key}>
                        <th>{dimensions[key] || key}</th>
                        <td>{v.baseline.toFixed(2)}</td>
                        <td>{v.learned.toFixed(2)}</td>
                        <td>
                          {v.delta >= 0 ? "+" : ""}
                          {v.delta.toFixed(2)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          <details>
            <summary>核对冻结的实验条件与经验</summary>
            <p>
              目标：{exp.painter_snapshot.growth_goal || "未设置"} · 风格：
              {exp.painter_snapshot.style}
            </p>
            <p>
              {exp.seed_controlled ? "配对生图种子已控制" : "生图种子未控制"}
              ；两组配置相同，模型仍可能有随机性。
            </p>
            <pre>{JSON.stringify(exp.conditions, null, 2)}</pre>
            <ul>
              {exp.memory_snapshot
                .flatMap((n) => n.rules.slice(0, 2))
                .map((r, i) => (
                  <li key={i}>{r.slice(0, 160)}</li>
                ))}
            </ul>
            <small>经验快照：{exp.memory_digest.slice(0, 16)}</small>
          </details>
          {Array.from({ length: exp.pairs }, (_, pair) => {
            const results = exp.results.filter((r) => r.pair === pair);
            if (!results.length) return null;
            return (
              <section key={pair}>
                <h4>第 {pair + 1} 对</h4>
                <div className="growth-pair">
                  {(["baseline", "learned"] as const).map((arm) => {
                    const result = results.find((r) => r.arm === arm);
                    return (
                      <div key={arm}>
                        <h4>
                          {exp.mode === "image_reference"
                            ? arm === "baseline"
                              ? "仅文字"
                              : "文字 + 参考图"
                            : arm === "baseline"
                              ? "未使用风格 / 经验"
                              : "学习组"}{" "}
                          · {result?.critique.overall.toFixed(2) || "等待"}
                        </h4>
                        {result && (
                          <>
                            <button
                              className="growth-art"
                              onClick={() => void open(result.entry_id)}
                            >
                              <img
                                loading="lazy"
                                src={asset(
                                  `/api/v1/painters/${painterId}/creations/${result.entry_id}/image`,
                                )}
                                alt={`第 ${pair + 1} 对${arm === "baseline" ? "基线" : "学习组"}作品，点击查看详情`}
                              />
                            </button>
                            <details>
                              <summary>查看看图记录与逐项评价</summary>
                              <p>{result.critique.feedback}</p>
                            </details>
                            <small>
                              种子：{result.seed ?? "未控制"} · 注入{" "}
                              {result.memory_used.length} 条经验
                            </small>
                          </>
                        )}
                      </div>
                    );
                  })}
                </div>
              </section>
            );
          })}
          {exp.failed_entry_id && (
            <button
              className="secondary"
              onClick={() => void open(exp.failed_entry_id!)}
            >
              查看未完成作品
            </button>
          )}
        </article>
      ))}
    </section>
  );
}
