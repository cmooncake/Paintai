import { useEffect, useState } from "react";
import { asset, post, request, type Job } from "./api";
import "./style-learning.css";

type Traits = {
  palette: string;
  lines: string;
  lighting: string;
  composition: string;
  texture: string;
  guidance: string[];
};
type Profile = {
  id: string;
  name: string;
  traits: Traits;
  reference_ids: string[];
  created_at: string;
};
type Reference = { id: string; image_url: string };
type Data = {
  references: Reference[];
  profiles: { profile: Profile; analysis_model: string }[];
  active_profile: Profile | null;
};
const labels = {
  palette: "配色",
  lines: "线条",
  lighting: "光影",
  composition: "构图",
  texture: "质感",
};

export default function StylePanel({
  painterId,
  disabled,
  onJob,
}: {
  painterId: string;
  disabled: boolean;
  onJob: (job: Job) => void;
}) {
  const [data, setData] = useState<Data | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [draft, setDraft] = useState<Profile | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const base = `/painters/${painterId}`;
  async function reload() {
    const next = await request<Data>(`${base}/styles`);
    setData(next);
    return next;
  }
  useEffect(() => {
    let disposed = false;
    request<Data>(`${base}/styles`)
      .then((v) => {
        if (!disposed) {
          setData(v);
          setDraft(
            (old) => old || v.active_profile || v.profiles[0]?.profile || null,
          );
        }
      })
      .catch((e) => {
        if (!disposed) setMessage(e.message);
      });
    return () => {
      disposed = true;
    };
  }, [base, disabled]);
  useEffect(() => {
    if (!job || !["queued", "running"].includes(job.status)) return;
    let disposed = false;
    const timer = window.setInterval(() => {
      request<Job>(`/jobs/${job.id}`)
        .then(async (next) => {
          if (disposed) return;
          setJob(next);
          if (next.status === "succeeded") {
            const value = await request<Data>(`${base}/styles`);
            if (!disposed) {
              setData(value);
              setDraft(
                value.profiles.find((p) => p.profile.id === next.id)?.profile ||
                  null,
              );
              setMessage("提取完成。检查或修改特点后，点击保存并启用。");
            }
          } else if (!["queued", "running"].includes(next.status))
            setMessage(next.error || "提取未完成，请检查视觉模型后重试。");
        })
        .catch((e) => {
          if (!disposed) setMessage(e.message);
        });
    }, 2500);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [job?.id, job?.status, base]);
  const locked =
    busy || disabled || (!!job && ["queued", "running"].includes(job.status));
  async function run(action: () => Promise<void>) {
    setBusy(true);
    setMessage("");
    try {
      await action();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="direction-card style-learning">
      <h3>参考图与风格档案</h3>
      <p>
        上传指定画师的作品，学习配色、线条、光影、构图和质感。每次选 1–3
        张，每张最多 10 MB（PNG / JPEG /
        WebP）。这是视觉分析与创作引导，不会训练模型权重。图片会交给当前配置的视觉评价模型分析。
      </p>
      <label htmlFor="style-reference-upload">上传参考作品</label>
      <input
        id="style-reference-upload"
        type="file"
        accept="image/png,image/jpeg,image/webp"
        multiple
        disabled={locked}
        onChange={(e) => {
          const files = Array.from(e.target.files || []);
          e.target.value = "";
          void run(async () => {
            if (files.length > 3) throw new Error("一次最多上传 3 张参考图。");
            if (files.some((f) => f.size > 10 * 1024 * 1024))
              throw new Error("每张参考图不能超过 10 MB。");
            const ids: string[] = [];
            try {
              for (const file of files) {
                const ref = await request<Reference>(`${base}/references`, {
                  method: "POST",
                  headers: { "Content-Type": "application/octet-stream" },
                  body: file,
                });
                ids.push(ref.id);
              }
            } finally {
              await reload();
              setSelected((old) =>
                Array.from(new Set([...ids, ...old])).slice(0, 3),
              );
            }
          });
        }}
      />
      <div className="reference-grid">
        {data?.references.map((r, i) => (
          <label key={r.id}>
            <img src={asset(r.image_url)} alt={`参考作品 ${i + 1}`} />
            <span>
              <input
                type="checkbox"
                checked={selected.includes(r.id)}
                disabled={
                  locked || (!selected.includes(r.id) && selected.length >= 3)
                }
                onChange={(e) =>
                  setSelected((old) =>
                    e.target.checked
                      ? [...old, r.id]
                      : old.filter((id) => id !== r.id),
                  )
                }
              />
              参考 {i + 1}
            </span>
          </label>
        ))}
      </div>
      <label htmlFor="style-profile-name">新档案名称</label>
      <input
        id="style-profile-name"
        maxLength={100}
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="例如：某画师参考 · 清透人物插画"
        disabled={locked}
      />
      <button
        className="secondary"
        disabled={locked || !selected.length || !name.trim()}
        onClick={() =>
          void run(async () => {
            const value = await post<Job>(
              `${base}/styles/analyze`,
              { name: name.trim(), reference_ids: selected },
              crypto.randomUUID(),
            );
            setJob(value);
            onJob(value);
            setMessage("正在看图提取风格，可在此等待结果。");
          })
        }
      >
        提取所选作品的画风
      </button>
      <p>
        当前启用：
        <strong>{data?.active_profile?.name || "未启用风格档案"}</strong>
      </p>
      {data?.active_profile && (
        <button
          className="secondary"
          disabled={locked}
          onClick={() =>
            void run(async () => {
              await post(`${base}/styles/activate`, { profile_id: null });
              await reload();
              setMessage("已停用，历史作品保留原风格档案。");
            })
          }
        >
          停用风格档案
        </button>
      )}
      {!!data?.profiles.length && (
        <label>
          查看已保存档案
          <select
            value={draft?.id || ""}
            disabled={locked}
            onChange={(e) =>
              setDraft(
                data.profiles.find((p) => p.profile.id === e.target.value)
                  ?.profile || null,
              )
            }
          >
            {data.profiles.map((p) => (
              <option value={p.profile.id} key={p.profile.id}>
                {p.profile.name} · {p.profile.id.slice(0, 8)}
              </option>
            ))}
          </select>
        </label>
      )}
      {draft && (
        <div className="style-traits">
          <h4>检查风格特点</h4>
          <label>
            档案名称
            <input
              value={draft.name}
              maxLength={100}
              disabled={locked}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            />
          </label>
          {(Object.keys(labels) as (keyof typeof labels)[]).map((k) => (
            <label key={k}>
              {labels[k]}
              <textarea
                value={draft.traits[k]}
                maxLength={1000}
                disabled={locked}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    traits: { ...draft.traits, [k]: e.target.value },
                  })
                }
              />
            </label>
          ))}
          <label>
            创作指导（每行一条，最多 6 条）
            <textarea
              value={draft.traits.guidance.join("\n")}
              disabled={locked}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  traits: {
                    ...draft.traits,
                    guidance: e.target.value.split("\n"),
                  },
                })
              }
            />
          </label>
          <p>
            启用后用于后续构思和看图评价；作品及创作记忆会保存当时的档案版本。
          </p>
          <button
            className="primary"
            disabled={locked}
            onClick={() =>
              void run(async () => {
                await post(`${base}/styles/activate`, {
                  profile_id: draft.id,
                  name: draft.name,
                  traits: {
                    ...draft.traits,
                    guidance: draft.traits.guidance
                      .map((s) => s.trim())
                      .filter(Boolean),
                  },
                });
                const next = await reload();
                setDraft(next.active_profile);
                setMessage("风格档案已保存并启用，后续创作将使用此版本。");
              })
            }
          >
            保存并启用风格档案
          </button>
        </div>
      )}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
