import { useCallback, useEffect, useRef, useState } from "react";
import { DirectionBrief } from "./Direction";
import type { FormEvent, ReactNode } from "react";
import { active, asset, post, request } from "./api";
import LearningPanel from "./LearningPanel";
import ModelSettingsPanel from "./ModelSettingsPanel";
import type { Creation, Job, Painter, System } from "./api";

const dimensions: Record<string, string> = {
  topic: "主题表达",
  technique: "技法表现",
  composition: "画面构图",
  color: "色彩和谐",
  style: "风格契合",
  creativity: "创意表达",
};
const stages: Record<string, string> = {
  queued: "等待画师落笔",
  running: "准备创作",
  planning: "正在构思画面",
  generating: "正在绘制作品",
  generated: "画面已完成",
  evaluating: "正在欣赏与评价",
  succeeded: "创作已完成",
  failed: "这次创作未完成",
  interrupted: "任务已中断",
};
const friendlyStatus: Record<string, string> = {
  complete: "已完成",
  planning: "构思中",
  generating: "绘制中",
  generated: "待评价",
  evaluating: "评价中",
  creation_failed: "创作未完成",
  evaluation_failed: "等待重新评价",
};
const inspirations = [
  {
    label: "秋日林间",
    text: "An autumn forest with golden leaves, a winding path and soft morning sunlight",
  },
  {
    label: "海边日落",
    text: "A quiet beach at sunset, soft pink clouds and gentle waves reflecting warm light",
  },
  {
    label: "窗边的猫",
    text: "An orange cat sitting beside a window, warm afternoon light and a small green plant",
  },
];
const styleOptions = [
  { label: "水彩", value: "watercolor painting" },
  { label: "油画", value: "oil painting" },
  { label: "水墨", value: "Chinese ink wash painting" },
  { label: "写实", value: "realistic illustration" },
  { label: "自由风格", value: "自由风格" },
];
const styleLabel = (style: string) =>
  styleOptions.find((s) => s.value === style)?.label || style;
const date = (s: string) =>
  new Date(s).toLocaleDateString("zh-CN", { month: "long", day: "numeric" });

function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    studio: (
      <>
        <path d="m3 11 9-8 9 8v10H3z" />
        <path d="M9 21v-7h6v7" />
      </>
    ),
    gallery: (
      <>
        <rect x="3" y="3" width="18" height="18" rx="3" />
        <path d="m3 16 5-5 4 4 4-6 5 7" />
        <circle cx="8" cy="7" r="1" />
      </>
    ),
    plus: <path d="M12 5v14M5 12h14" />,
    arrow: <path d="M4 12h15m-6-6 6 6-6 6" />,
    spark: (
      <>
        <path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z" />
      </>
    ),
    brush: (
      <>
        <path d="m15 3 6 6-10 10-6-6zM5 13c-4 1-1 5-3 9 4-2 8 1 9-3" />
      </>
    ),
    close: <path d="m6 6 12 12M6 18 18 6" />,
    check: <path d="m5 12 4 4L19 6" />,
    download: (
      <>
        <path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5" />
      </>
    ),
    clock: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 2" />
      </>
    ),
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name] || paths.spark}
    </svg>
  );
}

function Modal({
  title,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const node = dialog.current!;
    node.showModal();
    return () => node.close();
  }, []);
  return (
    <dialog
      ref={dialog}
      className={wide ? "modal wide" : "modal"}
      onCancel={onClose}
      onClick={(e) => {
        if (e.target === dialog.current) onClose();
      }}
    >
      <div className="modal-head">
        <span>{title}</span>
        <button className="icon-button" aria-label="关闭" onClick={onClose}>
          <Icon name="close" />
        </button>
      </div>
      {children}
    </dialog>
  );
}

export default function App() {
  const [painters, setPainters] = useState<Painter[]>([]);
  const [selected, setSelected] = useState("");
  const [entries, setEntries] = useState<Creation[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [system, setSystem] = useState<System | null>(null);
  const [view, setView] = useState<
    "studio" | "gallery" | "learning" | "models"
  >("studio");
  const [topic, setTopic] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [newPainter, setNewPainter] = useState(false);
  const [detail, setDetail] = useState<Creation | null>(null);
  const [filter, setFilter] = useState("all");
  const queryRef = useRef("");
  queryRef.current = `${selected}:${page}:${filter}`;
  const submitRef = useRef(false);
  const draftKey = useRef<{ signature: string; key: string } | null>(null);
  const painter = painters.find((p) => p.painter_id === selected);
  const running = jobs.find(active);
  const featured =
    entries.find(
      (e) => e.image_url && !e.simulation?.generator && e.status === "complete",
    ) || entries.find((e) => e.image_url);
  const [boot, setBoot] = useState(0);

  useEffect(() => {
    let disposed = false;
    setLoading(true);
    request<Painter[]>("/painters")
      .then((list) => {
        if (disposed) return;
        setPainters(list);
        setError("");
        const saved = localStorage.getItem("paintai-painter");
        setSelected(
          list.find((p) => p.painter_id === saved)?.painter_id ||
            list.find((p) => p.painter_id === "local_art")?.painter_id ||
            list[0]?.painter_id ||
            "",
        );
      })
      .catch((e) => {
        if (!disposed) setError(e.message);
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [boot]);

  useEffect(() => {
    let stopped = false;
    const load = () =>
      request<System>("/system")
        .then((s) => {
          if (!stopped) setSystem(s);
        })
        .catch(() => {
          if (!stopped) setSystem(null);
        });
    void load();
    const timer = setInterval(load, 15000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, []);

  const refresh = useCallback(async () => {
    if (!selected) return;
    const query = `${selected}:${page}:${filter}`;
    const [gallery, list] = await Promise.all([
      request<{ items: Creation[]; total: number }>(
        `/painters/${selected}/creations?limit=12&offset=${page * 12}&status=${filter}`,
      ),
      request<Job[]>(`/jobs?painter_id=${selected}`),
    ]);
    if (queryRef.current !== query) return;
    setEntries(gallery.items);
    setTotal(gallery.total);
    setJobs(list);
    setError("");
    setDetail((old) =>
      old ? gallery.items.find((e) => e.id === old.id) || old : null,
    );
  }, [selected, page, filter]);
  useEffect(() => {
    if (!selected) return;
    let stopped = false;
    setLoading(true);
    const load = async () => {
      try {
        await refresh();
      } catch (e) {
        if (!stopped) setError((e as Error).message);
      } finally {
        if (!stopped) setLoading(false);
      }
    };
    void load();
    const timer = setInterval(load, 2500);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [refresh, selected]);
  useEffect(() => {
    if (selected) localStorage.setItem("paintai-painter", selected);
  }, [selected]);
  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(""), 5000);
    return () => clearTimeout(t);
  }, [notice]);

  function selectPainter(id: string) {
    setSelected(id);
    setPage(0);
    setEntries([]);
    setJobs([]);
    setDetail(null);
    setError("");
  }
  async function create(e: FormEvent) {
    e.preventDefault();
    if (!topic.trim() || !painter || submitRef.current) return;
    submitRef.current = true;
    setSubmitting(true);
    setError("");
    const signature = selected + topic.trim();
    if (draftKey.current?.signature !== signature)
      draftKey.current = { signature, key: crypto.randomUUID() };
    try {
      const job = await post<Job>(
        "/creations",
        { painter_id: selected, topic: topic.trim() },
        draftKey.current.key,
      );
      setJobs((old) => [job, ...old.filter((j) => j.id !== job.id)]);
      setPage(0);
      setFilter("all");
      draftKey.current = null;
      setNotice("灵感已交给画师，作品会自动保存。");
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      submitRef.current = false;
      setSubmitting(false);
    }
  }
  async function evaluate(entry: Creation) {
    if (submitRef.current) return;
    submitRef.current = true;
    setSubmitting(true);
    try {
      const job = await post<Job>(
        `/painters/${entry.painter_id}/creations/${entry.id}/evaluate`,
        {},
        crypto.randomUUID(),
      );
      setJobs((old) => [job, ...old]);
      setNotice("已开始重新看图评价，原评价会保留。");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      submitRef.current = false;
      setSubmitting(false);
    }
  }
  const shown = entries.filter(
    (e) =>
      filter === "all" ||
      (filter === "complete"
        ? e.status === "complete"
        : e.status.includes("failed")),
  );
  const statusReady = system?.ready;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            setView("studio");
          }}
        >
          <span className="brand-mark">
            p<span>•</span>
          </span>
          <span>
            Paintai<small>让创作，持续生长</small>
          </span>
        </a>
        <div className="nav-label">我的空间</div>
        <nav aria-label="主导航">
          <button
            className={view === "models" ? "nav-item selected" : "nav-item"}
            onClick={() => setView("models")}
          >
            <Icon name="brush" />
            模型设置
          </button>
          <button
            className={view === "studio" ? "nav-item selected" : "nav-item"}
            onClick={() => setView("studio")}
          >
            <Icon name="studio" />
            画室
            <span className="nav-dot" />
          </button>
          <button
            className={view === "gallery" ? "nav-item selected" : "nav-item"}
            onClick={() => setView("gallery")}
          >
            <Icon name="gallery" />
            作品库<span className="nav-count">{total}</span>
          </button>
          <button
            className={view === "learning" ? "nav-item selected" : "nav-item"}
            onClick={() => setView("learning")}
          >
            <Icon name="brush" />
            学习
            <span className="nav-dot" />
          </button>
        </nav>
        <div className="sidebar-section">
          <div className="nav-label">
            我的画师
            <button
              aria-label="添加画师"
              className="small-add"
              onClick={() => setNewPainter(true)}
            >
              <Icon name="plus" size={16} />
            </button>
          </div>
          <div className="painter-list">
            {painters.map((p, i) => (
              <button
                key={p.painter_id}
                className={`painter-item ${p.painter_id === selected ? "active" : ""}`}
                onClick={() => selectPainter(p.painter_id)}
              >
                <span className={`avatar avatar-${i % 3}`}>
                  {p.name.slice(0, 1)}
                </span>
                <span>
                  <b>{p.name}</b>
                  <small>{styleLabel(p.style)}</small>
                </span>
                {p.painter_id === selected && (
                  <span className="selected-check">
                    <Icon name="check" size={14} />
                  </span>
                )}
              </button>
            ))}
          </div>
          <button className="new-painter" onClick={() => setNewPainter(true)}>
            <Icon name="plus" size={17} />
            创建一位画师
          </button>
        </div>
        <div className="sidebar-note">
          <span className="mini-spark">✳</span>
          <h3>每一次落笔，都是开始。</h3>
          <p>
            保留灵感与作品，慢慢找到
            <br />
            属于你的表达。
          </p>
        </div>
        <div className="sidebar-bottom">
          <span className={`status-dot ${statusReady ? "" : "off"}`} />
          <span>
            {system?.simulated
              ? "演示画室"
              : statusReady
                ? "本地画室已就绪"
                : "画室服务待连接"}
          </span>
          <span className="version">M2a</span>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div className="breadcrumb">
            我的空间 <span>/</span>{" "}
            <strong>
              {view === "models"
                ? "模型设置"
                : view === "studio"
                  ? "画室"
                  : view === "learning"
                    ? "学习手记"
                    : "作品库"}
            </strong>
          </div>
          <div className="topbar-right">
            <span className="quiet-tag">
              <Icon name="spark" size={14} />
              {system?.simulated ? "演示模式" : "本地创作"}
            </span>
            <span className="user-avatar">我</span>
          </div>
        </header>
        <div className="page-content">
          <section className="page-heading">
            <div>
              <div className="eyebrow">YOUR CREATIVE SPACE</div>
              <h1>
                {view === "models"
                  ? "为画师换一套更好的工具。"
                  : view === "learning"
                    ? "每一笔，都有新的收获。"
                    : view === "studio"
                      ? "今天，想画些什么？"
                      : "把灵感，收藏成作品。"}
              </h1>
              <p>
                {view === "models"
                  ? "独立配置构思、生图与视觉评价服务。"
                  : view === "learning"
                    ? "循着课程练习，让经验回到下一次创作。"
                    : view === "studio"
                      ? "从一个念头开始，让画师为你落笔。"
                      : "每一幅画，都留着一次探索的痕迹。"}
              </p>
            </div>
            <div className="heading-decoration" aria-hidden="true">
              ✳
            </div>
          </section>
          {error && (
            <div className="alert" role="alert">
              <span>{error}</span>
              <button
                onClick={() => {
                  if (!painters.length) setBoot((x) => x + 1);
                  else void refresh().catch((e) => setError(e.message));
                }}
              >
                重试连接
              </button>
            </div>
          )}
          {!statusReady && system && (
            <div className="service-message">
              <Icon name="clock" size={17} />
              有模型服务尚未就绪。作品仍可浏览，请在模型设置中检查服务地址与认证。
            </div>
          )}
          {view === "models" ? (
            <ModelSettingsPanel
              onSaved={() => {
                void request<System>("/system")
                  .then(setSystem)
                  .catch(() => {});
              }}
            />
          ) : !painters.length && !loading ? (
            <div className="welcome-empty">
              <span className="empty-icon">
                <Icon name="brush" size={40} />
              </span>
              <h2>你的第一位画师，等你命名</h2>
              <p>给它一个名字和喜欢的风格，就可以开始创作了。</p>
              <button className="primary" onClick={() => setNewPainter(true)}>
                <Icon name="plus" />
                创建画师
              </button>
            </div>
          ) : (
            <>
              {view === "learning" && painter && (
                <LearningPanel
                  key={selected}
                  painterId={selected}
                  painterName={painter.name}
                  running={running}
                  ready={!!statusReady}
                  onJob={(job) =>
                    setJobs((old) => [
                      job,
                      ...old.filter((j) => j.id !== job.id),
                    ])
                  }
                  onOpen={setDetail}
                />
              )}
              {view === "studio" && (
                <section className="studio-grid">
                  <div className="creation-panel">
                    <div className="section-kicker">
                      <span className="number">01</span> 一点灵感，一幅新作
                    </div>
                    <form onSubmit={create}>
                      <div className="form-label">
                        <label htmlFor="topic">描绘你脑海中的画面</label>
                        <span>{topic.length} / 1500</span>
                      </div>
                      <div className="prompt-wrap">
                        <textarea
                          id="topic"
                          maxLength={1500}
                          value={topic}
                          onChange={(e) => setTopic(e.target.value)}
                          placeholder="一片金色的树林，一条通向远方的小路，晨光穿过树叶……"
                        />
                        <span className="prompt-decoration">
                          <Icon name="brush" size={19} />
                        </span>
                      </div>
                      <div className="inspiration">
                        <span>试试这些灵感</span>
                        <div>
                          {inspirations.map((i) => (
                            <button
                              key={i.label}
                              type="button"
                              className="chip"
                              onClick={() => setTopic(i.text)}
                            >
                              {i.label}
                              <span>↗</span>
                            </button>
                          ))}
                        </div>
                      </div>
                      <div className="selected-artist">
                        <span className="avatar avatar-0">
                          {painter?.name.slice(0, 1) || "画"}
                        </span>
                        <div>
                          <span>
                            由 <strong>{painter?.name}</strong> 为你创作
                          </span>
                          <small>
                            {painter
                              ? styleLabel(painter.style)
                              : "选择一位画师"}{" "}
                            · 风格会随画师一起保存
                          </small>
                        </div>
                        <Icon name="brush" size={18} />
                      </div>
                      <button
                        className="primary create-button"
                        type="submit"
                        disabled={
                          !topic.trim() ||
                          !painter ||
                          submitting ||
                          !!running ||
                          !statusReady
                        }
                      >
                        <Icon name={running ? "clock" : "spark"} size={19} />
                        {running
                          ? "画师正在创作中"
                          : submitting
                            ? "正在提交灵感…"
                            : "开始创作"}
                        <Icon name="arrow" size={19} />
                      </button>
                      <p className="form-footnote">
                        作品会自动保存。当前小模型使用简短英文或灵感卡片更稳定。
                      </p>
                    </form>
                  </div>
                  <div className="featured-panel">
                    {featured?.image_url ? (
                      <>
                        <img
                          src={asset(featured.image_url)}
                          alt={featured.topic}
                        />
                        <div className="featured-shade" />
                        <div className="featured-top">
                          <span className="glass-tag">
                            <span />
                            来自你的作品库
                          </span>
                          <span className="image-index">
                            PAINTAI / COLLECTION
                          </span>
                        </div>
                        <div className="featured-caption">
                          <span className="featured-eyebrow">
                            一幅画里的片刻宁静
                          </span>
                          <h2>
                            {featured.topic.startsWith("An autumn")
                              ? "光，落在林间。"
                              : featured.topic}
                          </h2>
                          <div>
                            <span>
                              {painter?.name} · {date(featured.created_at)}
                            </span>
                            <button
                              onClick={() => setDetail(featured)}
                              aria-label="查看精选作品"
                            >
                              <Icon name="arrow" />
                            </button>
                          </div>
                        </div>
                      </>
                    ) : (
                      <div className="blank-canvas">
                        <span className="canvas-outline">
                          <Icon name="brush" size={54} />
                        </span>
                        <p>
                          留一方空白，
                          <br />
                          <em>给尚未到来的灵感。</em>
                        </p>
                        <span>你的第一幅作品将出现在这里</span>
                      </div>
                    )}
                  </div>
                </section>
              )}
              {view === "studio" && (
                <button
                  className="learning-link"
                  onClick={() => setView("learning")}
                >
                  <span>开始系统练习 · 查看课程与创作记忆</span>
                  <small>让下一幅画，有经验可循 →</small>
                </button>
              )}
              {running && (
                <section className="progress-panel" aria-live="polite">
                  <div className="progress-title">
                    <span className="spinner" />
                    <div>
                      <strong>
                        {running.training
                          ? `${running.training.name} ${running.training.completed || 0}/${running.training.total} · `
                          : ""}
                        {stages[running.stage] || "画师正在工作"}
                      </strong>
                      <p>
                        {running.kind === "evaluate"
                          ? "让画师重新欣赏这幅作品。"
                          : "构思、绘制、评价，每一步都值得等待。"}{" "}
                        关闭页面也不会取消任务。
                      </p>
                    </div>
                  </div>
                  <div className="steps">
                    {["planning", "generating", "evaluating", "succeeded"].map(
                      (step, i) => {
                        const current = [
                          "planning",
                          "generating",
                          "evaluating",
                          "succeeded",
                        ].indexOf(
                          running.stage === "generated"
                            ? "evaluating"
                            : running.stage,
                        );
                        return (
                          <span
                            key={step}
                            className={i <= current ? "step done" : "step"}
                          >
                            <i>
                              {i < current ? (
                                <Icon name="check" size={12} />
                              ) : (
                                i + 1
                              )}
                            </i>
                            {["构思", "绘制", "评价", "完成"][i]}
                          </span>
                        );
                      },
                    )}
                  </div>
                </section>
              )}
              {!running &&
                jobs[0] &&
                ["failed", "interrupted"].includes(jobs[0].status) && (
                  <div className="alert" role="status">
                    <span>{jobs[0].error}</span>
                    {jobs[0].entry_id && (
                      <button
                        onClick={() =>
                          void request<Creation>(
                            `/painters/${selected}/creations/${jobs[0].entry_id}`,
                          )
                            .then(setDetail)
                            .catch((e) => setError(e.message))
                        }
                      >
                        查看详情
                      </button>
                    )}
                  </div>
                )}
              <section className="collection">
                <div className="collection-head">
                  <div>
                    <div className="eyebrow">THE COLLECTION</div>
                    <h2>
                      {view === "studio" ? "最近的作品" : "我的作品"}
                      <span>{total}</span>
                    </h2>
                  </div>
                  <div className="filters" aria-label="作品筛选">
                    {[
                      ["all", "全部"],
                      ["complete", "已完成"],
                      ["failed", "待处理"],
                    ].map(([value, label]) => (
                      <button
                        key={value}
                        className={filter === value ? "active" : ""}
                        onClick={() => {
                          setFilter(value);
                          setPage(0);
                        }}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </div>
                {loading && !entries.length ? (
                  <div className="loading-state">
                    <span className="spinner" />
                    正在整理画室…
                  </div>
                ) : !shown.length ? (
                  <div className="gallery-empty">
                    <Icon name="gallery" size={32} />
                    <h3>
                      {total
                        ? "这里还没有符合条件的作品"
                        : "还没有作品，先画下第一个想法吧"}
                    </h3>
                    <p>创作完成后，画面和评价都会被好好收藏。</p>
                  </div>
                ) : (
                  <div className="art-grid">
                    {shown.map((entry, index) => (
                      <button
                        key={entry.id}
                        className="art-card"
                        onClick={() => setDetail(entry)}
                      >
                        <div className="art-image">
                          {entry.image_url ? (
                            <img
                              loading="lazy"
                              src={asset(entry.image_url)}
                              alt={entry.topic}
                            />
                          ) : (
                            <div className="art-placeholder">
                              <Icon
                                name={
                                  entry.status.includes("failed")
                                    ? "brush"
                                    : "clock"
                                }
                                size={28}
                              />
                              <span>
                                {friendlyStatus[entry.status] || "创作中"}
                              </span>
                            </div>
                          )}
                          <span
                            className={`art-badge ${entry.simulated ? "demo" : ""}`}
                          >
                            {entry.simulated
                              ? "含模拟环节"
                              : friendlyStatus[entry.status] || "创作中"}
                          </span>
                          <span className="art-hover">
                            查看作品 <Icon name="arrow" size={16} />
                          </span>
                        </div>
                        <div className="art-info">
                          <div className="art-meta">
                            <span>
                              NO.{" "}
                              {String(page * 12 + index + 1).padStart(2, "0")}
                            </span>
                            <span>{date(entry.created_at)}</span>
                          </div>
                          <h3>
                            {entry.learning?.lesson_name
                              ? `${entry.learning.lesson_name} · 练习 ${entry.learning.exercise}`
                              : entry.topic}
                          </h3>
                          <div className="art-bottom">
                            <span>
                              {painter ? styleLabel(painter.style) : ""}
                            </span>
                            {entry.critique ? (
                              <span className="score">
                                <Icon name="spark" size={13} />
                                {entry.critique.overall.toFixed(1)}
                                <small>/ 10</small>
                              </span>
                            ) : (
                              <span className="pending-score">
                                {entry.status.includes("failed")
                                  ? "待处理"
                                  : "等待评价"}
                              </span>
                            )}
                          </div>
                        </div>
                      </button>
                    ))}
                  </div>
                )}
                {total > 12 && (
                  <div className="pagination">
                    <button
                      disabled={!page}
                      onClick={() => setPage((p) => p - 1)}
                    >
                      上一页
                    </button>
                    <span>
                      {page + 1} / {Math.ceil(total / 12)}
                    </span>
                    <button
                      disabled={(page + 1) * 12 >= total}
                      onClick={() => setPage((p) => p + 1)}
                    >
                      下一页
                    </button>
                  </div>
                )}
              </section>
              <footer className="page-footer">
                <span>Paintai · 为每一次灵感留一个位置</span>
                <span>从创作开始，向自己的风格靠近。</span>
              </footer>
            </>
          )}
        </div>
      </main>
      {notice && (
        <div className="toast" role="status">
          <Icon name="check" size={18} />
          {notice}
        </div>
      )}
      {newPainter && (
        <NewPainterModal
          onClose={() => setNewPainter(false)}
          onCreated={(p) => {
            setPainters((old) => [...old, p]);
            selectPainter(p.painter_id);
            setNewPainter(false);
            setNotice("新的画师已加入画室。");
          }}
        />
      )}
      {detail && (
        <Modal title="作品与创作笔记" wide onClose={() => setDetail(null)}>
          <div className="detail-grid">
            <div className="detail-visual">
              {detail.image_url ? (
                <img src={asset(detail.image_url)} alt={detail.topic} />
              ) : (
                <div className="detail-no-image">
                  <Icon name="brush" size={44} />
                  <p>这次创作尚未生成图片</p>
                </div>
              )}
              <div className="detail-image-foot">
                <span>
                  {date(detail.created_at)} · {friendlyStatus[detail.status]}
                </span>
                {detail.image_url && (
                  <a
                    href={asset(detail.image_url)}
                    download={`paintai-${detail.id}.png`}
                    className="download-link"
                  >
                    <Icon name="download" size={16} />
                    保存图片
                  </a>
                )}
              </div>
            </div>
            <div className="detail-copy">
              <DirectionBrief brief={detail.art_brief} />
              {detail.experiment && (
                <p className="learning-help">
                  成长实验 · 第 {detail.experiment.pair + 1} 对 ·{" "}
                  {detail.experiment.arm === "baseline"
                    ? "无经验基线"
                    : "使用学习经验"}
                  。此作品不写入训练记忆，重评不会改写原实验结论。
                </p>
              )}
              {detail.learning && (
                <div className="memory-reference">
                  课程练习 ·{" "}
                  {detail.learning.lesson_name || detail.learning.lesson_id} ·
                  第 {detail.learning.exercise} 幅
                </div>
              )}
              {!!detail.memory_used?.length && (
                <details className="memory-reference">
                  <summary>
                    本次参考了 {detail.memory_used.length} 条创作记忆
                  </summary>
                  {detail.memory_used.map((note) => (
                    <div key={note.entry_id}>
                      <p>{note.topic}</p>
                      {note.rules.map((rule, i) => (
                        <p key={i}>· {rule}</p>
                      ))}
                    </div>
                  ))}
                </details>
              )}
              {error && (
                <div className="alert" role="alert">
                  {error}
                </div>
              )}
              <div className="eyebrow">CREATIVE NOTES</div>
              <h2>
                {detail.learning?.lesson_name
                  ? `${detail.learning.lesson_name} · 练习 ${detail.learning.exercise}`
                  : detail.topic}
              </h2>
              {detail.learning && (
                <details>
                  <summary>查看本次题目</summary>
                  <p>{detail.topic}</p>
                </details>
              )}
              {detail.simulated && (
                <p className="simulation-note">
                  本作品包含模拟环节，模拟评分不代表真实质量。
                </p>
              )}
              {detail.critique ? (
                <>
                  <div className="overall">
                    <strong>{detail.critique.overall.toFixed(2)}</strong>
                    <span>
                      / 10
                      <br />
                      <small>模型综合评价</small>
                    </span>
                    <span className="overall-spark">✳</span>
                  </div>
                  <div className="score-bars">
                    {Object.entries(dimensions).map(([key, label]) => (
                      <div className="score-row" key={key}>
                        <span>{label}</span>
                        <div>
                          <i
                            style={{
                              width: `${(detail.critique?.scores[key] || 0) * 10}%`,
                            }}
                          />
                        </div>
                        <b>{detail.critique?.scores[key]}</b>
                      </div>
                    ))}
                  </div>
                  <h3>画师的观察</h3>
                  <p className="feedback">{detail.critique.feedback}</p>
                  <h3>下一次，可以试试</h3>
                  <ol className="improvements">
                    {detail.critique.improvements.map((tip, i) => (
                      <li key={i}>
                        <span>{String(i + 1).padStart(2, "0")}</span>
                        {tip}
                      </li>
                    ))}
                  </ol>
                  <p className="rating-note">
                    评价是模型的主观看法，可作为创作参考。
                  </p>
                </>
              ) : (
                <div className="no-critique">
                  <h3>
                    {detail.image_url
                      ? "画面已留下，评价还在路上"
                      : "这次灵感还未完成"}
                  </h3>
                  <p>
                    {detail.image_url
                      ? "你可以在服务就绪后重新评价。"
                      : "试着简化描述，回到画室重新开始。"}
                  </p>
                </div>
              )}
              {detail.can_evaluate && (
                <button
                  className="secondary"
                  disabled={
                    !!running || submitting || !system?.roles.critic.ready
                  }
                  onClick={() => void evaluate(detail)}
                >
                  <Icon name="spark" size={17} />
                  {running?.kind === "evaluate"
                    ? "正在重新评价…"
                    : "重新看图评价"}
                </button>
              )}
              {detail.plan && (
                <details>
                  <summary>查看创作构思</summary>
                  <p>{detail.plan.composition}</p>
                  <p>{detail.plan.palette}</p>
                  <div className="prompt-note">{detail.plan.prompt}</div>
                  {detail.generation_constraints && (
                    <>
                      <h4>本轮保留的人物要求</h4>
                      <ul>
                        {Object.entries(
                          detail.generation_constraints.protected_attributes,
                        ).map(([key, value]) => (
                          <li key={key}>
                            {(
                              {
                                hair: "发色与发型",
                                eyes: "眼睛",
                                clothing: "服装",
                                background: "背景",
                              } as Record<string, string>
                            )[key] || key}
                            ：{value}
                          </li>
                        ))}
                      </ul>
                      <p>
                        风格只补充绘画技巧；是否落实到画面仍以看图核对为准。
                      </p>
                      <details>
                        <summary>查看排除的生图元素</summary>
                        <div className="prompt-note">
                          {detail.generation_constraints.negative_prompt}
                        </div>
                      </details>
                    </>
                  )}
                </details>
              )}
              {detail.evaluation_history.filter((h) => h.critique).length >
                1 && (
                <details>
                  <summary>评价历史</summary>
                  {detail.evaluation_history
                    .filter((h) => h.critique)
                    .map((h, i) => (
                      <p key={i}>
                        {h.finished_at
                          ? new Date(h.finished_at).toLocaleString("zh-CN")
                          : ""}{" "}
                        <strong>{h.critique!.overall.toFixed(2)} / 10</strong>
                      </p>
                    ))}
                </details>
              )}
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

function NewPainterModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (p: Painter) => void;
}) {
  const [name, setName] = useState("");
  const [style, setStyle] = useState("watercolor painting");
  const [goal, setGoal] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    try {
      onCreated(
        await post<Painter>("/painters", {
          name: name.trim(),
          style,
          growth_goal: goal,
        }),
      );
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }
  return (
    <Modal title="认识一位新画师" onClose={onClose}>
      <form className="painter-form" onSubmit={submit}>
        <span className="form-illustration">
          <Icon name="brush" size={32} />
        </span>
        <h2>
          从名字开始，
          <br />
          赋予它自己的风格。
        </h2>
        <label htmlFor="painter-name">画师的名字</label>
        <input
          autoFocus
          id="painter-name"
          required
          maxLength={60}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="比如：小森、拾光、墨白"
        />
        <label htmlFor="painter-style">喜欢的绘画风格</label>
        <select
          id="painter-style"
          value={style}
          onChange={(e) => setStyle(e.target.value)}
        >
          {styleOptions.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
        <label htmlFor="new-growth-goal">长期成长目标（选填）</label>
        <textarea
          id="new-growth-goal"
          maxLength={400}
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="例如：擅长清淡水彩山水，重点学习空间层次，避免颜色过饱和。"
        />
        <p>
          每位画师拥有独立的目标、作品库和创作记录。目标可在课程学习中修改。
        </p>
        {error && (
          <div className="alert" role="alert">
            {error}
          </div>
        )}
        <button className="primary" disabled={!name.trim() || busy}>
          {busy ? "正在创建…" : "邀请画师加入"}
          <Icon name="arrow" size={18} />
        </button>
      </form>
    </Modal>
  );
}
