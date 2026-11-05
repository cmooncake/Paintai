import { useEffect, useRef, useState } from "react";
import { post, request } from "./api";
import type { Creation, Job } from "./api";
import "./learning.css";
import { DirectionBrief, GoalEditor } from "./Direction";
import StylePanel from "./StylePanel";
import type { ArtBrief } from "./Direction";
import GrowthPanel from "./GrowthPanel";

type Round = {
  tasks?: string[];
  assessment?: boolean;
  art_brief?: ArtBrief;
  failed_entry_id?: string;
  id: string;
  name: string;
  status: string;
  passed: boolean;
  simulated: boolean;
  average: number | null;
  created_at: string;
  results: { entry_id: string; score: number }[];
};
type Lesson = {
  id: string;
  name: string;
  task: string;
  skills: string[];
  pass_score: number;
  status: string;
  rounds: number;
  latest: Round | null;
};
type Progress = {
  growth_goal: string;
  spec: { title: string };
  stages: { id: string; name: string; goals: string[]; lessons: Lesson[] }[];
  current: { lesson_id: string; name: string } | null;
  completed: string[];
  total: number;
  percent: number;
  history: Round[];
};
type Note = {
  art_brief?: ArtBrief;
  id: string;
  entry_id: string;
  topic: string;
  score: number;
  simulated: boolean;
  rules: string[];
  feedback: string;
  skills: string[];
  evaluation_count: number;
};

export default function LearningPanel({
  painterId,
  painterName,
  running,
  ready,
  onJob,
  onOpen,
}: {
  painterId: string;
  painterName: string;
  running: Job | undefined;
  ready: boolean;
  onJob: (job: Job) => void;
  onOpen: (entry: Creation) => void;
}) {
  const [progress, setProgress] = useState<Progress | null>(null);
  const [notes, setNotes] = useState<Note[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editor, setEditor] = useState(false);
  const [draft, setDraft] = useState("");
  const [tab, setTab] = useState<"course" | "memory" | "growth">("course");
  const [refresh, setRefresh] = useState(0);
  const [instructions, setInstructions] = useState("");
  const [focus, setFocus] = useState("");
  const [difficulty, setDifficulty] = useState("standard");
  const [count, setCount] = useState(3);
  const [preview, setPreview] = useState<{
    lesson_id: string;
    name: string;
    tasks: string[];
    assessment: boolean;
    art_brief: ArtBrief;
  } | null>(null);
  const options = { instructions, focus, difficulty, count };
  const pending = useRef(false);
  const previewPanel = useRef<HTMLElement>(null);
  useEffect(() => {
    if (preview)
      previewPanel.current?.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
  }, [preview]);
  const requestKey = useRef<{ lesson: string; key: string } | null>(null);
  useEffect(() => {
    let disposed = false;
    const load = async () => {
      try {
        const [p, m] = await Promise.all([
          request<Progress>(`/painters/${painterId}/learning`),
          request<{ items: Note[] }>(`/painters/${painterId}/memory`),
        ]);
        if (!disposed) {
          setProgress(p);
          setNotes(m.items);
        }
      } catch (e) {
        if (!disposed) setError((e as Error).message);
      }
    };
    void load();
    const timer = setInterval(load, 2500);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [painterId, refresh]);
  async function train(lesson: string) {
    if (pending.current || running) return;
    pending.current = true;
    setBusy(true);
    setError("");
    const signature = JSON.stringify({ lesson, ...options });
    if (requestKey.current?.lesson !== signature)
      requestKey.current = { lesson: signature, key: crypto.randomUUID() };
    try {
      onJob(
        await post<Job>(
          `/painters/${painterId}/train`,
          { lesson_id: lesson, ...options },
          requestKey.current.key,
        ),
      );
      requestKey.current = null;
      setPreview(null);
      setRefresh((x) => x + 1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  async function prepare(lesson: string) {
    setBusy(true);
    setError("");
    try {
      setPreview(
        await post(`/painters/${painterId}/train/preview`, {
          lesson_id: lesson,
          ...options,
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function open(entryId: string) {
    try {
      onOpen(
        await request<Creation>(`/painters/${painterId}/creations/${entryId}`),
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function sync() {
    setBusy(true);
    setError("");
    try {
      await post(`/painters/${painterId}/memory/sync`, {});
      setRefresh((x) => x + 1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    setBusy(true);
    setError("");
    try {
      const body = JSON.parse(draft);
      setProgress(
        await post<Progress>(`/painters/${painterId}/curriculum`, body),
      );
      setEditor(false);
    } catch (e) {
      setError(
        e instanceof SyntaxError
          ? "课程内容不是有效的 JSON，请检查格式。"
          : (e as Error).message,
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="learning-space">
      <StylePanel
        key={painterId}
        painterId={painterId}
        disabled={!!running || busy}
        onJob={onJob}
      />
      {progress && (
        <GoalEditor
          key={progress.growth_goal}
          painterId={painterId}
          goal={progress.growth_goal}
          disabled={!!running || busy}
          onSaved={() => {
            setPreview(null);
            setRefresh((x) => x + 1);
          }}
        />
      )}
      <div className="learning-tabs">
        <button
          className={tab === "growth" ? "active" : ""}
          onClick={() => setTab("growth")}
        >
          成长实验
        </button>
        <button
          className={tab === "course" ? "active" : ""}
          onClick={() => setTab("course")}
        >
          课程学习
        </button>
        <button
          className={tab === "memory" ? "active" : ""}
          onClick={() => setTab("memory")}
        >
          创作记忆{" "}
          <span>
            {notes.length}
            {notes.length === 100 ? "+" : ""}
          </span>
        </button>
      </div>
      {error && (
        <div className="alert" role="alert">
          {error}
          <button
            onClick={() => {
              setError("");
              setRefresh((x) => x + 1);
            }}
          >
            重试
          </button>
        </div>
      )}
      {tab === "growth" ? (
        <GrowthPanel
          painterId={painterId}
          running={running}
          ready={ready}
          onJob={onJob}
          onOpen={onOpen}
        />
      ) : !progress ? (
        <div className="loading-state">正在打开学习手记…</div>
      ) : tab === "course" ? (
        <>
          <section className="direction-card" onChange={() => setPreview(null)}>
            <h3>本轮练习要求</h3>
            <p>
              仅影响本轮，不会覆盖长期目标。标准考核为 3
              幅、标准难度且不附加要求；自定义为专项练习，留下评价和记忆，但不计入关卡通过。
            </p>
            <label htmlFor="practice-focus">重点能力（选填）</label>
            <input
              id="practice-focus"
              maxLength={100}
              value={focus}
              onChange={(e) => setFocus(e.target.value)}
              placeholder="例如：冷暖对比、远近层次"
              disabled={busy || !!running}
            />
            <label htmlFor="practice-instructions">
              自然语言练习要求（选填）
            </label>
            <textarea
              id="practice-instructions"
              maxLength={400}
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              placeholder="例如：远山用冷色，近景用暖色，保持构图简单。"
              disabled={busy || !!running}
            />
            <div className="practice-options">
              <label>
                练习难度
                <select
                  value={difficulty}
                  onChange={(e) => setDifficulty(e.target.value)}
                  disabled={busy || !!running}
                >
                  <option value="gentle">入门</option>
                  <option value="standard">标准</option>
                  <option value="challenge">挑战</option>
                </select>
              </label>
              <label>
                作品数量
                <select
                  value={count}
                  onChange={(e) => setCount(Number(e.target.value))}
                  disabled={busy || !!running}
                >
                  {[1, 2, 3, 4, 5, 6].map((n) => (
                    <option key={n} value={n}>
                      {n} 幅
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <p>填写后，选择下方关卡预览题目。</p>
          </section>
          {preview && (
            <section
              ref={previewPanel}
              className="direction-card"
              role="region"
              aria-label="练习预览"
            >
              <h3>
                {preview.name} · {preview.assessment ? "标准考核" : "专项练习"}
              </h3>
              <DirectionBrief brief={preview.art_brief} />
              <ol>
                {preview.tasks.map((task, i) => (
                  <li key={i}>{task}</li>
                ))}
              </ol>
              <button
                className="primary"
                disabled={busy || !!running || !ready}
                onClick={() => void train(preview.lesson_id)}
              >
                开始 {preview.tasks.length} 幅练习
              </button>
              <button className="secondary" onClick={() => setPreview(null)}>
                取消
              </button>
            </section>
          )}
          <div className="learning-overview">
            <div>
              <div className="eyebrow">ONE LESSON AT A TIME</div>
              <h2>{progress.spec.title}</h2>
              <p>{painterName}的学习路线 · 标准考核 3 幅，通过后解锁下一关</p>
              <div
                className="course-meter"
                role="progressbar"
                aria-label="课程完成进度"
                aria-valuenow={progress.percent}
                aria-valuemin={0}
                aria-valuemax={100}
              >
                <i style={{ width: `${progress.percent}%` }} />
              </div>
              <span className="course-count">
                {progress.completed.length} / {progress.total} 关已通过
              </span>
            </div>
            <div className="course-percent">
              {progress.percent}
              <small>%</small>
              <span>学习进度</span>
            </div>
          </div>
          <div className="learning-help">
            {running?.kind === "train"
              ? `正在练习「${running.training?.name || ""}」，已完成 ${running.training?.completed || 0} / ${running.training?.total || 3} 幅。离开页面后仍会继续。`
              : progress.current
                ? `下一课：${progress.current.name}。按本轮三幅作品的六维平均分判断是否过关。`
                : "这份课程已完成，仍可选择任一关复习。"}{" "}
            模拟结果不计入过关。
          </div>
          <div className="course-stages">
            {progress.stages.map((stage, index) => (
              <section className="course-stage" key={stage.id}>
                <header>
                  <span className="stage-number">0{index + 1}</span>
                  <div>
                    <h3>{stage.name}</h3>
                    <p>{stage.goals.join(" · ")}</p>
                  </div>
                  <span className="stage-count">
                    {stage.lessons.filter((l) => l.status === "passed").length}{" "}
                    / {stage.lessons.length}
                  </span>
                </header>
                <div className="lesson-list">
                  {stage.lessons.map((lesson) => (
                    <article
                      key={lesson.id}
                      className={`lesson ${lesson.status}`}
                    >
                      <div className="lesson-indicator">
                        {lesson.status === "passed"
                          ? "✓"
                          : lesson.status === "current"
                            ? "○"
                            : "·"}
                      </div>
                      <div className="lesson-body">
                        <div className="lesson-title">
                          <h4>{lesson.name}</h4>
                          <span>
                            {lesson.status === "passed"
                              ? "已通过"
                              : lesson.status === "current"
                                ? "当前关卡"
                                : "未解锁"}
                          </span>
                        </div>
                        <p>{lesson.task}</p>
                        {progress.growth_goal && (
                          <p>
                            题目将结合长期目标调整；具体内容以练习预览为准。
                          </p>
                        )}
                        <div className="lesson-meta">
                          <span>过关 ≥ {lesson.pass_score} 分</span>
                          <span>{lesson.rounds} 轮练习</span>
                          {lesson.latest && (
                            <span>
                              {lesson.latest.status === "complete"
                                ? (lesson.latest.simulated
                                    ? "模拟练习"
                                    : lesson.latest.passed
                                      ? "本轮通过"
                                      : lesson.latest.assessment === false
                                        ? "专项练习"
                                        : "本轮待加强") +
                                  " · " +
                                  lesson.latest.average?.toFixed(2)
                                : lesson.latest.status === "running"
                                  ? `练习中 ${lesson.latest.results.length}/${lesson.latest.tasks?.length || 3}`
                                  : lesson.latest.status === "interrupted"
                                    ? "上次练习中断"
                                    : "上次练习未完成"}
                            </span>
                          )}
                        </div>
                        {lesson.latest && (
                          <div className="lesson-artworks">
                            {lesson.latest.results.map((r, i) => (
                              <button
                                key={r.entry_id}
                                onClick={() => void open(r.entry_id)}
                              >
                                练习 {i + 1} · {r.score.toFixed(1)} 分 ↗
                              </button>
                            ))}
                            {lesson.latest.failed_entry_id && (
                              <button
                                onClick={() =>
                                  void open(lesson.latest!.failed_entry_id!)
                                }
                              >
                                查看未完成的练习 ↗
                              </button>
                            )}
                          </div>
                        )}
                      </div>
                      <button
                        className={
                          lesson.status === "current" ? "primary" : "secondary"
                        }
                        disabled={
                          lesson.status === "locked" ||
                          !!running ||
                          busy ||
                          !ready
                        }
                        onClick={() => void prepare(lesson.id)}
                      >
                        {lesson.status === "passed"
                          ? "再练一轮"
                          : lesson.status === "locked"
                            ? "待解锁"
                            : busy
                              ? "正在提交…"
                              : "预览本关练习"}
                      </button>
                    </article>
                  ))}
                </div>
              </section>
            ))}
          </div>
          <details className="training-history">
            <summary>练习记录 · 最近 {progress.history.length} 轮</summary>
            {!progress.history.length ? (
              <p>完成第一轮练习后，这里会留下记录。</p>
            ) : (
              progress.history.map((r) => (
                <div className="history-row" key={r.id}>
                  <span>
                    {r.name}
                    <DirectionBrief brief={r.art_brief} />
                    <small>
                      {new Date(r.created_at).toLocaleString("zh-CN")}
                    </small>
                  </span>
                  <span>
                    {r.status === "complete"
                      ? r.simulated
                        ? "模拟"
                        : r.passed
                          ? "通过"
                          : r.assessment === false
                            ? "专项练习"
                            : "待加强"
                      : `${r.status === "failed" ? "未完成" : r.status === "interrupted" ? "已中断" : "练习中"} ${r.results.length}/${r.tasks?.length || 3}`}
                  </span>
                  <strong>{r.average?.toFixed(2) ?? "—"}</strong>
                </div>
              ))
            )}
          </details>
          <div className="course-custom">
            <button
              disabled={!!running || busy || !progress.growth_goal}
              onClick={async () => {
                setBusy(true);
                setError("");
                try {
                  const spec = await post(
                    `/painters/${painterId}/curriculum/preview`,
                    {},
                  );
                  setDraft(JSON.stringify(spec, null, 2));
                  setEditor(true);
                } catch (e) {
                  setError((e as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              按已保存目标准备课程草案
            </button>
            <button
              onClick={() => {
                setDraft(JSON.stringify(progress.spec, null, 2));
                setEditor(!editor);
              }}
              disabled={!!running || busy}
            >
              {editor ? "收起课程编辑" : "自定义课程"}
            </button>
            <p>
              草案按固定课程模板组织，可在下方审阅、修改并保存。改动关卡要求后，该关需要重新练习。
            </p>
          </div>
          {editor && (
            <div className="curriculum-editor">
              <label htmlFor="curriculum-json">课程规格（JSON）</label>
              <textarea
                id="curriculum-json"
                value={draft}
                maxLength={100000}
                onChange={(e) => setDraft(e.target.value)}
                spellCheck={false}
              />
              <p>
                阶段与关卡 ID 必须唯一，依赖只能指向前面的关卡。CLI 同时支持
                YAML。
              </p>
              <button
                className="primary"
                onClick={() => void save()}
                disabled={busy || !!running}
              >
                保存课程
              </button>
            </div>
          )}
        </>
      ) : (
        <>
          <div className="memory-heading">
            <div>
              <div className="eyebrow">NOTES FOR THE NEXT PAINTING</div>
              <h2>让每一次尝试，留下经验。</h2>
              <p>从视觉评价提取建议，下次创作时按主题和技能选择相关经验。</p>
            </div>
            <button
              className="secondary"
              onClick={() => void sync()}
              disabled={busy || !!running}
            >
              {busy ? "整理中…" : "整理历史作品"}
            </button>
          </div>
          <div className="learning-help">
            记忆来自模型反馈，仍需实践验证。模拟记录可查看，但不会用于后续创作。
          </div>
          {!notes.length ? (
            <div className="gallery-empty">
              <h3>还没有创作记忆</h3>
              <p>完成一次创作会自动留下经验，也可以整理之前的作品。</p>
            </div>
          ) : (
            <div className="memory-grid">
              {notes.map((note) => (
                <article className="memory-card" key={note.id}>
                  <div className="memory-card-top">
                    <span>{note.simulated ? "模拟记录" : "创作经验"}</span>
                    <strong>
                      {note.score.toFixed(1)} <small>/ 10</small>
                    </strong>
                  </div>
                  <h3>
                    {note.art_brief?.practice?.focus ||
                      note.art_brief?.growth_goal ||
                      note.topic}
                  </h3>
                  {note.art_brief && (
                    <details>
                      <summary>查看原始题目</summary>
                      <p>{note.topic}</p>
                    </details>
                  )}
                  <DirectionBrief brief={note.art_brief} />
                  <p>{note.feedback}</p>
                  <h4>下一次可以参考</h4>
                  <ul>
                    {note.rules.map((rule, i) => (
                      <li key={i}>{rule}</li>
                    ))}
                  </ul>
                  <footer>
                    <span>
                      {note.evaluation_count} 次评价 ·{" "}
                      {note.skills.length ? "课程练习" : "自由创作"}
                    </span>
                    <button onClick={() => void open(note.entry_id)}>
                      查看来源作品 ↗
                    </button>
                  </footer>
                </article>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}
