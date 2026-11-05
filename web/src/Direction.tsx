import { useState } from "react";
import { post } from "./api";

export type ArtBrief = {
  style_profile?: { id: string; name: string; traits: { guidance: string[] } };
  growth_goal?: string;
  stage_goals?: string[];
  skills?: string[];
  practice?: {
    instructions: string;
    focus: string;
    difficulty: string;
    count: number;
  };
};
export function DirectionBrief({ brief }: { brief?: ArtBrief | null }) {
  if (!brief) return null;
  return (
    <div className="direction-brief">
      {brief.style_profile && (
        <p>
          <strong>参考风格：</strong>
          {brief.style_profile.name} · 版本 {brief.style_profile.id.slice(0, 8)}
          <br />
          {brief.style_profile.traits.guidance.join("；")}
        </p>
      )}
      {brief.growth_goal && (
        <p>
          <strong>长期目标：</strong>
          {brief.growth_goal}
        </p>
      )}
      {brief.practice && (
        <p>
          <strong>本轮要求：</strong>
          {[brief.practice.focus, brief.practice.instructions]
            .filter(Boolean)
            .join(" · ") || "按课程标准练习"}{" "}
          ·{" "}
          {
            { gentle: "入门", standard: "标准", challenge: "挑战" }[
              brief.practice.difficulty
            ]
          }{" "}
          · {brief.practice.count} 幅
        </p>
      )}
    </div>
  );
}
export function GoalEditor({
  painterId,
  goal,
  disabled,
  onSaved,
}: {
  painterId: string;
  goal: string;
  disabled: boolean;
  onSaved: () => void;
}) {
  const [value, setValue] = useState(goal);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  async function save() {
    setBusy(true);
    try {
      await post(`/painters/${painterId}/goal`, { growth_goal: value });
      setMessage("目标已保存，后续出题、构思和评价都会参考。");
      onSaved();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="direction-card">
      <h3>长期成长目标</h3>
      <label htmlFor="growth-goal">希望这位画师逐渐擅长什么？</label>
      <textarea
        id="growth-goal"
        maxLength={400}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="例如：学习清淡水彩山水，重点练习空间层次，避免颜色过饱和。"
        disabled={disabled || busy}
      />
      <p>
        可直接用自然语言描述题材、风格、能力与偏好。修改目标会按新方向计算进度，旧作品和练习记录保留；切回原目标可查看原进度。
      </p>
      <button
        className="secondary"
        disabled={disabled || busy || value === goal}
        onClick={() => void save()}
      >
        保存成长目标
      </button>
      {message && <p role="status">{message}</p>}
    </section>
  );
}
